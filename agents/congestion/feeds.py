"""Congestion inputs reconstructed from immutable stored evidence, without feed I/O."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from data.adsb import AdsbObservation, AdsbSnapshot
from data.faa_status import EventType, FaaEvent, FaaStatusSnapshot
from data.flightaware import Association, DemandRecord, FlightDirection, TimeKind
from shared.persistence import utc
from shared.settings import AirportConfig
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
    snapshot_id: str = ""
    case_id: str = ""
    evidence_ids: tuple[str, ...] = ()
    traffic_evidence_id: str | None = None
    faa_evidence_ids: tuple[tuple[str, str], ...] = ()
    configuration: AirportConfig = AirportConfig()
    window_start_utc: datetime | None = None
    window_end_utc: datetime | None = None
    expires_at_utc: datetime | None = None
    demand_records: tuple[DemandRecord, ...] | None = None
    demand_fetched_at_utc: datetime | None = None
    demand_evidence_id: str | None = None


def stored_feeds(value: dict[str, Any]) -> FeedSnapshot:
    """Reconstruct parser DTOs from server-copied batches, never latest live rows."""
    adsb = None
    faa = None
    traffic_id = None
    demand = None
    demand_fetched = None
    demand_id = None
    faa_ids: list[tuple[str, str]] = []
    start = utc(value["window_start_utc"])
    health = {row["provider"]: row for row in value["health"]}
    errors = list(value["limitations"])
    for batch in value["batches"]:
        payload = batch["payload"]
        provider = payload["provider"]
        if provider == "flightaware":
            demand_id = batch["batch_id"]
            if health.get(provider, {}).get("status") == "ok":
                demand_fetched = utc(payload["fetched_at_utc"])
                demand = tuple(
                    DemandRecord(
                        flight_id=r["flight_id"],
                        direction=FlightDirection(r["direction"]),
                        time_utc=utc(r["time_utc"]) if r["time_utc"] else None,
                        time_kind=TimeKind(r["time_kind"]) if r["time_kind"] else None,
                        association=Association(r["association"]),
                        cancelled=r["cancelled"],
                    )
                    for r in payload["records"]
                )
            continue
        source = dict(payload["snapshot"])
        fetched = utc(source["fetched_at_utc"])
        if provider == "adsb_lol":
            traffic_id = batch["batch_id"]
            if health.get(provider, {}).get("status") != "ok":
                continue
            observations = []
            for item in source.pop("observations"):
                row = dict(item)
                for key in ("observed_at_utc", "provider_time_utc", "fetched_at_utc"):
                    row[key] = utc(row[key]) if row[key] is not None else None
                for key in ("missing_fields", "quality_flags"):
                    row[key] = tuple(row[key])
                observations.append(AdsbObservation(**row))
            source["provider_time_utc"] = (
                utc(source["provider_time_utc"]) if source["provider_time_utc"] else None
            )
            source["fetched_at_utc"] = fetched
            adsb = AdsbSnapshot(**source, observations=tuple(observations))
        elif provider == "faa":
            if health.get(provider, {}).get("status") != "ok" or start - fetched > timedelta(
                minutes=5
            ):
                continue
            events = []
            for item in source.pop("events"):
                row = dict(item)
                for key in ("start_utc", "end_utc"):
                    row[key] = utc(row[key]) if row[key] else None
                row["event_type"] = EventType(row["event_type"])
                row["quality_flags"] = tuple(row["quality_flags"])
                row["raw_times"] = tuple(tuple(pair) for pair in row["raw_times"])
                event = FaaEvent(**row)
                events.append(event)
                if event.airport_icao_normalized == value["configuration"]["airport_icao"]:
                    faa_ids.append((event.event_key, f"{batch['batch_id']}:{event.event_key}"))
            source["source_update_time_utc"] = (
                utc(source["source_update_time_utc"]) if source["source_update_time_utc"] else None
            )
            source["fetched_at_utc"] = fetched
            source["unrecognized_sections"] = tuple(source["unrecognized_sections"])
            faa = FaaStatusSnapshot(**source, events=tuple(events))
    return FeedSnapshot(
        adsb,
        faa,
        tuple(errors),
        value["snapshot_id"],
        value["case_id"],
        tuple(value["evidence_ids"]),
        traffic_id,
        tuple(faa_ids),
        AirportConfig(**value["configuration"]),
        start,
        utc(value["window_end_utc"]),
        utc(value["expires_at_utc"]),
        demand,
        demand_fetched,
        demand_id,
    )
