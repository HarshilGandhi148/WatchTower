"""Interim live input for the congestion agent: public feeds plus optional AeroAPI.

Interim because the planned path is provider ingestion -> SpacetimeDB -> agent snapshot
reads (data/ingestion.py, shared/spacetimedb_adapter.py). Replace ``fetch_feeds`` with a
snapshot read once that path carries these records; the rest of the agent is unaffected.

Each feed fails independently to ``None`` with a recorded error: a failed feed is
unknown, never an empty one. AeroAPI is only called when configured, through a budget
and rate guard, and its result is cached so polling stays within the agreed spend.
"""

from __future__ import annotations

import json
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from data.adsb import AdsbParseError, AdsbSnapshot, parse_point_response, point_url
from data.faa_status import FaaParseError, FaaStatusSnapshot, parse_status_xml
from data.flightaware import (
    AeroApiBudget,
    AeroApiConfig,
    AeroApiParseError,
    DemandRecord,
    Endpoint,
    build_request,
    parse_flights,
)

TIMEOUT_S = 10
MAX_RESPONSE_BYTES = 5_000_000
AEROAPI_REFRESH = timedelta(minutes=5)  # plan section 10: schedule freshness budget 5 min
# Query windows per endpoint. Departures filter on scheduled_off, so look back 2 hours (the
# endpoint default) to keep delayed and taxiing flights for the backlog. Arrivals filter on
# estimated_on; past estimates fall outside every demand window, so start at now (fewer
# billed records, less chance of a truncated page).
AEROAPI_LOOKBACK = {
    Endpoint.SCHEDULED_DEPARTURES: timedelta(hours=2),
    Endpoint.SCHEDULED_ARRIVALS: timedelta(0),
}
AEROAPI_LOOKAHEAD = timedelta(minutes=60)

HttpGet = Callable[[str, str], bytes]  # (url, user_agent) -> body
HeaderGet = Callable[[str, Mapping[str, str]], bytes]  # (url, headers) -> body


@dataclass(frozen=True, slots=True)
class FeedConfig:
    adsb_base_url: str
    faa_url: str
    user_agent: str
    center: tuple[float, float]
    radius_nm: int = 40


@dataclass(frozen=True, slots=True)
class FeedSnapshot:
    adsb: AdsbSnapshot | None
    faa: FaaStatusSnapshot | None
    errors: tuple[str, ...] = ()
    flights: tuple[DemandRecord, ...] | None = None  # None = unavailable, never "no flights"
    flights_fetched_at_utc: datetime | None = None


def http_get_headers(url: str, headers: Mapping[str, str]) -> bytes:
    """Bounded HTTPS GET. Raises on any failure. Never logs headers (they may hold keys)."""
    if not url.startswith("https://"):
        raise ValueError("only https URLs are fetched")
    request = urllib.request.Request(url, headers=dict(headers))
    with urllib.request.urlopen(request, timeout=TIMEOUT_S) as response:  # noqa: S310
        body = response.read(MAX_RESPONSE_BYTES + 1)
    if not isinstance(body, bytes) or len(body) > MAX_RESPONSE_BYTES:
        raise ValueError("response missing or too large")
    return body


def http_get(url: str, user_agent: str) -> bytes:
    """Bounded HTTPS GET with an identifying User-Agent."""
    return http_get_headers(url, {"User-Agent": user_agent})


class AeroApiFetcher:
    """Cached, budget-guarded AeroAPI demand fetch (scheduled departures and arrivals).

    A refresh costs one reserved request per endpoint. If the budget or rate limit refuses,
    a request fails, or a page cap truncates the list, the result is unavailable (``None``)
    rather than a partial count that would understate demand.
    """

    def __init__(
        self,
        config: AeroApiConfig,
        user_agent: str,
        *,
        budget: AeroApiBudget | None = None,
        get: HeaderGet = http_get_headers,
        refresh: timedelta = AEROAPI_REFRESH,
    ) -> None:
        self._config = config
        self._user_agent = user_agent
        self._budget = budget or AeroApiBudget(config)
        self._get = get
        self._refresh = refresh
        self._cached: tuple[tuple[DemandRecord, ...], datetime] | None = None

    @property
    def budget(self) -> AeroApiBudget:
        return self._budget

    def fetch(
        self, now: datetime
    ) -> tuple[tuple[DemandRecord, ...] | None, datetime | None, str | None]:
        """Return (records, fetched_at, error). Serves the cache inside the refresh period."""
        if self._cached is not None and now - self._cached[1] < self._refresh:
            return self._cached[0], self._cached[1], None
        records: list[DemandRecord] = []
        for endpoint in (Endpoint.SCHEDULED_DEPARTURES, Endpoint.SCHEDULED_ARRIVALS):
            pages = self._budget.reserve(self._config.max_pages_per_request, now)
            if pages == 0:
                return None, None, "aeroapi: budget or rate limit reached; demand unavailable"
            request = build_request(
                self._config,
                endpoint,
                now - AEROAPI_LOOKBACK[endpoint],
                now + AEROAPI_LOOKAHEAD,
                pages,
                self._user_agent,
            )
            try:
                page = parse_flights(
                    json.loads(self._get(request.url, request.headers)), endpoint, now
                )
            except (OSError, ValueError, AeroApiParseError) as error:
                # The URL and headers are not logged: the key travels in a header.
                return None, None, f"aeroapi {endpoint.value}: {type(error).__name__}"
            if page.has_more:
                return (
                    None,
                    None,
                    (
                        f"aeroapi {endpoint.value}: more flights than FLIGHTAWARE_MAX_PAGES "
                        "fetched; demand unavailable rather than undercounted"
                    ),
                )
            records.extend(page.records)
        self._cached = (tuple(records), now)
        return self._cached[0], now, None


def fetch_feeds(
    config: FeedConfig,
    get: HttpGet = http_get,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    flights: AeroApiFetcher | None = None,
) -> FeedSnapshot:
    """Fetch and normalize every feed; the fetch time is taken after each response."""
    errors: list[str] = []
    adsb: AdsbSnapshot | None = None
    faa: FaaStatusSnapshot | None = None
    try:
        url = point_url(config.adsb_base_url, *config.center, config.radius_nm)
        body = get(url, config.user_agent)
        adsb = parse_point_response(json.loads(body), clock())
    except (OSError, ValueError, AdsbParseError) as error:
        errors.append(f"adsb: {type(error).__name__}: {error}")
    try:
        faa = parse_status_xml(get(config.faa_url, config.user_agent), clock())
    except (OSError, ValueError, FaaParseError) as error:
        errors.append(f"faa: {type(error).__name__}: {error}")
    records: tuple[DemandRecord, ...] | None = None
    fetched_at: datetime | None = None
    if flights is not None:
        records, fetched_at, error_text = flights.fetch(clock())
        if error_text:
            errors.append(error_text)
    return FeedSnapshot(adsb, faa, tuple(errors), records, fetched_at)
