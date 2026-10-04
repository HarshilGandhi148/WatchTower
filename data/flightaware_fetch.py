"""Optional budgeted AeroAPI fetch helper; no runtime polling is enabled.

The caller must supply a bounded, credential-safe transport. Shared ingestion currently
polls only ADS-B and FAA; congestion consumes stored snapshots exclusively.
"""

import json
from collections.abc import Callable, Mapping
from datetime import datetime, timedelta

from data.flightaware import (
    AeroApiBudget,
    AeroApiConfig,
    AeroApiParseError,
    DemandRecord,
    Endpoint,
    build_request,
    parse_flights,
)

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

HeaderGet = Callable[[str, Mapping[str, str]], bytes]


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
        get: HeaderGet,
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
