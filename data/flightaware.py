"""FlightAware AeroAPI adapter: capped request building and schedule parsing.

Pure functions plus a caller-owned budget guard; no network, clock or database access.
The poller performs the HTTP call (bounded timeout) and passes the decoded JSON here.

Rules (plan 4.5, 10):
- Disabled unless ENABLE_FLIGHTAWARE=true AND a key, a USD budget and the per-result-set
  price are all configured. AeroAPI is usage-billed per result set (15 records), so every
  request is checked against the remaining budget and the per-minute result-set limit
  *before* it is sent, and page counts are always explicit.
- Only flights the KDTW airport endpoints return are records, so association is a
  confirmed flight record; a record whose origin/destination is not KDTW is rejected.
- Runway times are preferred over gate times; gate-only times are labelled so the metrics
  require an explicit taxi assumption. Missing times stay ``None``. Cancelled and diverted
  flights are kept but flagged, never counted as demand.
- Nothing here invents a record when AeroAPI is disabled, over budget or unavailable.

Checked against the AeroAPI 4.30.0 OpenAPI spec
(https://static.flightaware.com/rsrc/aeroapi/aeroapi-openapi.yml): the endpoint paths,
``x-apikey`` header, ``start``/``end``/``max_pages``/``cursor`` parameters, the response
keys (``scheduled_departures``/``scheduled_arrivals``, ``links.next``, ``num_pages``) and
every flight field used here. ``start``/``end`` filter on ``scheduled_off`` for departures
(default start: 2 hours ago) and on ``estimated_on`` for arrivals (default start: 48 hours
ago; includes en-route flights). ``start`` may be at most 10 days back and 2 days ahead.
Per the spec, ``cancelled`` means FlightAware stopped tracking the flight, which is not
always an airline cancellation; either way it is not counted as demand.

Still to confirm with a key: the 15-records-per-result-set billing unit (pricing page, not
the spec) and the per-result-set price for these endpoints (set in .env, never assumed).
"""

from __future__ import annotations

import math
from collections import deque
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from typing import Any
from urllib.parse import parse_qs, urlencode, urlsplit

SOURCE_ID = "flightaware-aeroapi"
AIRPORT_ICAO = "KDTW"
RECORDS_PER_RESULT_SET = 15  # pricing page: one billed result set = 15 records
PERSONAL_RESULT_SETS_PER_MINUTE = 10  # Personal tier limit from the public pricing page
MAX_WINDOW = timedelta(hours=4)
MAX_PAGES_PER_REQUEST = 5


class FlightDirection(StrEnum):
    ARRIVAL = "arrival"
    DEPARTURE = "departure"


class TimeKind(StrEnum):
    """What the time field means. Gate times are not runway times (plan 4.5)."""

    SCHEDULED_GATE = "scheduled_gate"
    SCHEDULED_RUNWAY = "scheduled_runway"
    ESTIMATED_RUNWAY = "estimated_runway"
    ACTUAL_RUNWAY = "actual_runway"


class Association(StrEnum):
    """Whether the flight-to-KDTW link comes from a flight record or was inferred."""

    CONFIRMED_RECORD = "confirmed_record"
    INFERRED = "inferred"


def _check_utc(value: datetime | None, name: str) -> None:
    if value is not None and value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be an aware UTC datetime")


@dataclass(frozen=True, slots=True)
class DemandRecord:
    """One airport-associated flight. A missing time is ``None``, never a default.

    ``time_utc``/``time_kind`` is the best runway-related time for demand windows.
    ``gate_scheduled_utc``/``gate_actual_utc`` are scheduled/actual gate out (departures)
    or gate in (arrivals), used for backlog estimates. ``other_airport_icao`` is the
    destination of a departure or the origin of an arrival, as the record states it.
    """

    flight_id: str
    direction: FlightDirection
    time_utc: datetime | None
    time_kind: TimeKind | None
    association: Association
    cancelled: bool = False
    diverted: bool = False
    other_airport_icao: str | None = None
    gate_scheduled_utc: datetime | None = None
    gate_actual_utc: datetime | None = None
    notes: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.flight_id, str) or not self.flight_id.strip():
            raise ValueError("flight_id must be a non-empty string")
        for name in ("time_utc", "gate_scheduled_utc", "gate_actual_utc"):
            _check_utc(getattr(self, name), name)
        if (self.time_utc is None) != (self.time_kind is None):
            raise ValueError("time_utc and time_kind must be given together")

    @property
    def excluded_from_demand(self) -> bool:
        """Cancelled or diverted flights are never counted as demand."""
        return self.cancelled or self.diverted


# --------------------------------------------------------------------- configuration


class AeroApiConfigError(ValueError):
    """AeroAPI is enabled but not safely configured."""


@dataclass(frozen=True, slots=True)
class AeroApiConfig:
    base_url: str
    api_key: str = field(repr=False)
    budget_usd: Decimal
    cost_per_result_set_usd: Decimal
    max_pages_per_request: int = 1
    result_sets_per_minute: int = PERSONAL_RESULT_SETS_PER_MINUTE

    def __post_init__(self) -> None:
        if not self.base_url.startswith("https://"):
            raise AeroApiConfigError("FLIGHTAWARE_BASE_URL must be https")
        if not self.api_key.strip() or any(c.isspace() for c in self.api_key):
            raise AeroApiConfigError("FLIGHTAWARE_API_KEY is missing or malformed")
        if not self.budget_usd > 0:
            raise AeroApiConfigError("FLIGHTAWARE_BUDGET_USD must be a positive amount")
        if not self.cost_per_result_set_usd > 0:
            raise AeroApiConfigError("FLIGHTAWARE_COST_PER_RESULT_SET_USD must be positive")
        if not 1 <= self.max_pages_per_request <= MAX_PAGES_PER_REQUEST:
            raise AeroApiConfigError(f"FLIGHTAWARE_MAX_PAGES must be 1..{MAX_PAGES_PER_REQUEST}")
        if self.result_sets_per_minute < 1:
            raise AeroApiConfigError("result_sets_per_minute must be positive")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AeroApiConfig | None:
        """``None`` when disabled. Raises if enabled without a key, budget and price."""
        if env.get("ENABLE_FLIGHTAWARE", "false").strip().lower() != "true":
            return None
        return cls(
            base_url=env.get("FLIGHTAWARE_BASE_URL", "").strip().rstrip("/"),
            api_key=env.get("FLIGHTAWARE_API_KEY", "").strip(),
            budget_usd=_money(env.get("FLIGHTAWARE_BUDGET_USD", ""), "FLIGHTAWARE_BUDGET_USD"),
            cost_per_result_set_usd=_money(
                env.get("FLIGHTAWARE_COST_PER_RESULT_SET_USD", ""),
                "FLIGHTAWARE_COST_PER_RESULT_SET_USD",
            ),
            max_pages_per_request=_int(
                env.get("FLIGHTAWARE_MAX_PAGES", "1"), "FLIGHTAWARE_MAX_PAGES"
            ),
        )


def _money(text: str, name: str) -> Decimal:
    try:
        value = Decimal(text.strip())
    except InvalidOperation as error:
        raise AeroApiConfigError(f"{name} must be set to a USD amount") from error
    if not value.is_finite():
        raise AeroApiConfigError(f"{name} must be finite")
    return value


def _int(text: str, name: str) -> int:
    try:
        return int(text.strip())
    except ValueError as error:
        raise AeroApiConfigError(f"{name} must be an integer") from error


# --------------------------------------------------------------------- budget guard


class AeroApiBudget:
    """Caller-owned spend and rate guard. Reserve pages *before* each request.

    Spend is process-local: a restart resets it unless ``already_spent_usd`` is passed in
    (for example read back from the AeroAPI portal's usage page). Set the budget so a full
    day of capped polling stays within what the team agreed.
    """

    def __init__(self, config: AeroApiConfig, already_spent_usd: Decimal = Decimal("0")) -> None:
        if already_spent_usd < 0:
            raise ValueError("already_spent_usd cannot be negative")
        self._config = config
        self._spent = already_spent_usd
        self._recent: deque[tuple[datetime, int]] = deque()

    @property
    def spent_usd(self) -> Decimal:
        return self._spent

    @property
    def remaining_usd(self) -> Decimal:
        return max(Decimal("0"), self._config.budget_usd - self._spent)

    def reserve(self, wanted_pages: int, now: datetime) -> int:
        """Return how many pages (result sets) may be requested now; 0 means skip.

        Reserved pages count as spent immediately: a failed request may still be billed.
        """
        _check_utc(now, "now")
        if wanted_pages < 1:
            return 0
        while self._recent and now - self._recent[0][0] >= timedelta(minutes=1):
            self._recent.popleft()
        used_this_minute = sum(pages for _, pages in self._recent)
        by_rate = self._config.result_sets_per_minute - used_this_minute
        by_money = math.floor(self.remaining_usd / self._config.cost_per_result_set_usd)
        pages = min(wanted_pages, self._config.max_pages_per_request, by_rate, by_money)
        if pages <= 0:
            return 0
        self._spent += self._config.cost_per_result_set_usd * pages
        self._recent.append((now, pages))
        return pages


# --------------------------------------------------------------------- requests


class Endpoint(StrEnum):
    """Future-demand endpoints only (flights not yet departed / not yet arrived)."""

    SCHEDULED_DEPARTURES = "scheduled_departures"
    SCHEDULED_ARRIVALS = "scheduled_arrivals"

    @property
    def direction(self) -> FlightDirection:
        if self is Endpoint.SCHEDULED_DEPARTURES:
            return FlightDirection.DEPARTURE
        return FlightDirection.ARRIVAL


# Response list key per endpoint (AeroAPI 4.30.0 spec).
_RESPONSE_KEYS = {
    Endpoint.SCHEDULED_DEPARTURES: "scheduled_departures",
    Endpoint.SCHEDULED_ARRIVALS: "scheduled_arrivals",
}


@dataclass(frozen=True, slots=True)
class AeroApiRequest:
    url: str
    headers: Mapping[str, str] = field(repr=False)  # contains the API key: never log
    pages: int


def build_request(
    config: AeroApiConfig,
    endpoint: Endpoint,
    start: datetime,
    end: datetime,
    pages: int,
    user_agent: str,
    cursor: str | None = None,
) -> AeroApiRequest:
    """Build one bounded request. ``pages`` must come from ``AeroApiBudget.reserve``."""
    _check_utc(start, "start")
    _check_utc(end, "end")
    if not timedelta(0) < end - start <= MAX_WINDOW:
        raise ValueError(f"query window must be positive and at most {MAX_WINDOW}")
    if not 1 <= pages <= config.max_pages_per_request:
        raise ValueError("pages must be reserved from the budget first")
    params: dict[str, str] = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "max_pages": str(pages),
    }
    if cursor:
        params["cursor"] = cursor
    url = f"{config.base_url}/airports/{AIRPORT_ICAO}/flights/{endpoint.value}?{urlencode(params)}"
    return AeroApiRequest(
        url=url,
        headers={
            "x-apikey": config.api_key,
            "Accept": "application/json",
            "User-Agent": user_agent,
        },
        pages=pages,
    )


# --------------------------------------------------------------------- parsing


class AeroApiParseError(ValueError):
    """Unusable response (distinct from a valid page with no flights)."""


# AeroAPI 4.30.0 BaseFlight field names.
_FIELDS = {
    "id": "fa_flight_id",
    "ident": "ident",
    "origin": "origin",
    "destination": "destination",
    "airport_icao": "code_icao",
    "cancelled": "cancelled",
    "diverted": "diverted",
    "scheduled_out": "scheduled_out",
    "actual_out": "actual_out",
    "scheduled_off": "scheduled_off",
    "estimated_off": "estimated_off",
    "actual_off": "actual_off",
    "scheduled_on": "scheduled_on",
    "estimated_on": "estimated_on",
    "actual_on": "actual_on",
    "scheduled_in": "scheduled_in",
    "actual_in": "actual_in",
}


@dataclass(frozen=True, slots=True)
class AeroApiPage:
    endpoint: Endpoint
    fetched_at_utc: datetime
    records: tuple[DemandRecord, ...]
    has_more: bool  # links.next present: more flights exist than were fetched
    next_cursor: str | None
    rejected_records: int
    duplicates_dropped: int


def parse_flights(payload: object, endpoint: Endpoint, fetched_at_utc: datetime) -> AeroApiPage:
    """Normalize one decoded response page into DemandRecords."""
    _check_utc(fetched_at_utc, "fetched_at_utc")
    if not isinstance(payload, dict):
        raise AeroApiParseError("response is not a JSON object")
    flights = payload.get(_RESPONSE_KEYS[endpoint])
    if not isinstance(flights, list):
        raise AeroApiParseError(f"response has no {_RESPONSE_KEYS[endpoint]} list")
    records: dict[str, DemandRecord] = {}
    rejected = duplicates = 0
    for flight in flights:
        record = _parse_flight(flight, endpoint.direction)
        if record is None:
            rejected += 1
        elif record.flight_id in records:
            duplicates += 1
        else:
            records[record.flight_id] = record
    return AeroApiPage(
        endpoint=endpoint,
        fetched_at_utc=fetched_at_utc,
        records=tuple(records.values()),
        has_more=_has_more(payload.get("links")),
        next_cursor=_next_cursor(payload.get("links")),
        rejected_records=rejected,
        duplicates_dropped=duplicates,
    )


def _has_more(links: object) -> bool:
    return isinstance(links, dict) and isinstance(links.get("next"), str) and bool(links["next"])


def _next_cursor(links: object) -> str | None:
    if not isinstance(links, dict):
        return None
    nxt = links.get("next")
    if not isinstance(nxt, str) or not nxt:
        return None
    values = parse_qs(urlsplit(nxt).query).get("cursor")
    return values[0] if values else None


def _airport(flight: dict[str, Any], key: str) -> str | None:
    value = flight.get(_FIELDS[key])
    if not isinstance(value, dict):
        return None
    code = value.get(_FIELDS["airport_icao"])
    if not isinstance(code, str) or not code.strip():
        return None
    code = code.strip().upper()
    return code if code.isalnum() and 3 <= len(code) <= 4 else None


def _time(flight: dict[str, Any], key: str, notes: list[str]) -> datetime | None:
    value = flight.get(_FIELDS[key])
    if value is None:
        return None
    if not isinstance(value, str):
        notes.append(f"invalid_{key}")
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        notes.append(f"invalid_{key}")
        return None
    if parsed.utcoffset() is None:
        notes.append(f"naive_{key}")
        return None
    return parsed.astimezone(UTC)


def _parse_flight(flight: object, direction: FlightDirection) -> DemandRecord | None:
    if not isinstance(flight, dict):
        return None
    flight_id = flight.get(_FIELDS["id"])
    if not isinstance(flight_id, str) or not flight_id.strip():
        return None
    origin, destination = _airport(flight, "origin"), _airport(flight, "destination")
    departing = direction is FlightDirection.DEPARTURE
    if (origin if departing else destination) != AIRPORT_ICAO:
        return None  # not actually a KDTW flight in this direction
    notes: list[str] = []
    if departing:
        candidates = (
            ("actual_off", TimeKind.ACTUAL_RUNWAY),
            ("estimated_off", TimeKind.ESTIMATED_RUNWAY),
            ("scheduled_off", TimeKind.SCHEDULED_RUNWAY),
            ("scheduled_out", TimeKind.SCHEDULED_GATE),
        )
        gate_scheduled = _time(flight, "scheduled_out", notes)
        gate_actual = _time(flight, "actual_out", notes)
    else:
        candidates = (
            ("actual_on", TimeKind.ACTUAL_RUNWAY),
            ("estimated_on", TimeKind.ESTIMATED_RUNWAY),
            ("scheduled_on", TimeKind.SCHEDULED_RUNWAY),
            ("scheduled_in", TimeKind.SCHEDULED_GATE),
        )
        gate_scheduled = _time(flight, "scheduled_in", notes)
        gate_actual = _time(flight, "actual_in", notes)
    time_utc: datetime | None = None
    time_kind: TimeKind | None = None
    for key, kind in candidates:
        value = _time(flight, key, notes)
        if value is not None:
            time_utc, time_kind = value, kind
            break
    cancelled = flight.get(_FIELDS["cancelled"]) is True
    diverted = flight.get(_FIELDS["diverted"]) is True
    return DemandRecord(
        flight_id=flight_id.strip(),
        direction=direction,
        time_utc=time_utc,
        time_kind=time_kind,
        association=Association.CONFIRMED_RECORD,
        cancelled=cancelled,
        diverted=diverted,
        other_airport_icao=destination if departing else origin,
        gate_scheduled_utc=gate_scheduled,
        gate_actual_utc=gate_actual,
        notes=tuple(dict.fromkeys(notes)),
    )
