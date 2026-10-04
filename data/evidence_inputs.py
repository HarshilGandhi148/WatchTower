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
    # True only when every stored flight record carried gate_scheduled_utc and
    # gate_actual_utc (possibly null), so the departure backlog can be observed.
    demand_gate_times: bool = False


def _optional_utc(record: dict[str, Any], key: str) -> datetime | None:
    value = record.get(key)
    return utc(value) if value else None


def stored_feeds(value: dict[str, Any]) -> FeedSnapshot:
    """Reconstruct parser DTOs from server-copied batches, never latest live rows."""
    adsb = None
    faa = None
    traffic_id = None
    demand = None
    demand_fetched = None
    demand_id = None
    gate_times = False
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
                        gate_scheduled_utc=_optional_utc(r, "gate_scheduled_utc"),
                        gate_actual_utc=_optional_utc(r, "gate_actual_utc"),
                    )
                    for r in payload["records"]
                )
                gate_times = bool(payload["records"]) and all(
                    "gate_scheduled_utc" in r and "gate_actual_utc" in r for r in payload["records"]
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
        gate_times,
    )
