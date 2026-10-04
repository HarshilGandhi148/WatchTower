"""Load one ValidationPacket from stored records for the pure validate_packet checks.

Evidence provenance comes from the snapshot's immutable, server-copied content, never
from live tables. An ID gets the case snapshot only if that snapshot lists it, so a
reference outside the snapshot fails the evidence checks. A record that does not exist
is ``None`` (insufficient data); an unavailable store raises, and the gateway closes the
case as validation_unavailable.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from agents.validation.packet import (
    EvidenceKind,
    EvidenceRecord,
    ProjectionPacket,
    ValidationPacket,
    WeatherFinding,
)
from agents.validation.storage import ValidationStorage
from shared.contracts import FlowCandidate, MessageEnvelope
from shared.orchestration import CandidateReady
from shared.persistence import candidate_from_dict, utc
from shared.spacetimedb_adapter import SpacetimeAdapter

PROVIDER_KINDS = {
    "adsb_lol": EvidenceKind.TRAFFIC,
    "flightaware": EvidenceKind.DEMAND,
    "faa": EvidenceKind.FAA_EVENT,
    "awc": EvidenceKind.WEATHER,
}
OUTSIDE_SNAPSHOT = "not-in-case-snapshot"


async def _one(
    adapter: SpacetimeAdapter, table: str, key: str, value: str
) -> dict[str, Any] | None:
    rows = await adapter.read_records(table, key=key, value=value)
    return rows[0] if len(rows) == 1 else None


def _batch_fetched(payload: dict[str, Any]) -> datetime:
    if "fetched_at_utc" in payload:  # flight records
        return utc(payload["fetched_at_utc"])
    return utc(payload["snapshot"]["fetched_at_utc"])  # ADS-B and FAA parser snapshots


def snapshot_evidence(snapshot: dict[str, Any]) -> dict[str, EvidenceRecord]:
    """EvidenceRecords for every ID the stored snapshot lists, from its copied content."""
    snapshot_id = snapshot["snapshot_id"]
    airport = snapshot["configuration"]["airport_icao"]
    listed = set(snapshot["evidence_ids"])
    records: dict[str, EvidenceRecord] = {}

    def add(
        evidence_id: str, kind: EvidenceKind, data_mode: str, fetched: datetime, **times: Any
    ) -> None:
        if evidence_id in listed:
            records[evidence_id] = EvidenceRecord(
                evidence_id=evidence_id,
                kind=kind,
                airport_icao=airport,
                snapshot_id=snapshot_id,
                data_mode=data_mode,
                fetched_at_utc=fetched,
                **times,
            )

    for batch in snapshot["batches"]:
        payload = batch["payload"]
        kind = PROVIDER_KINDS.get(payload["provider"])
        if kind is None:
            continue
        fetched = _batch_fetched(payload)
        mode = payload.get("data_mode", "unknown")
        if kind is EvidenceKind.FAA_EVENT:
            # The batch itself and each listed event (``<batch_id>:<event_key>``).
            add(batch["batch_id"], kind, mode, fetched)
            prefix = f"{batch['batch_id']}:"
            for evidence_id in listed:
                if evidence_id.startswith(prefix):
                    add(evidence_id, kind, mode, fetched)
        else:
            add(batch["batch_id"], kind, mode, fetched)
    for weather in snapshot.get("weather", ()):
        add(
            f"{snapshot_id}:weather:{weather['provider']}",
            EvidenceKind.WEATHER,
            "live",  # the observation table only accepts live writes
            utc(weather["fetched_at_utc"]),
            source_time_utc=utc(weather["observed_at_utc"]),
        )
    return records


async def load_packet(
    adapter: SpacetimeAdapter,
    storage: ValidationStorage,
    opening: MessageEnvelope,
    ready: CandidateReady,
    now: datetime,
) -> ValidationPacket:
    candidate_row = await _one(adapter, "flow_candidates", "candidate_id", ready.candidate_id)
    candidate: FlowCandidate | None = (
        candidate_from_dict(json.loads(candidate_row["content_json"])) if candidate_row else None
    )
    snapshot = await adapter.load_snapshot(ready.envelope.snapshot_id)
    evidence = snapshot_evidence(snapshot)

    projection: ProjectionPacket | None = None
    if candidate is not None and candidate.projection_id is not None:
        row = await _one(adapter, "projection_results", "projection_id", candidate.projection_id)
        if row is not None:
            flow, assumptions, _, comparison = await storage.projection_record(
                candidate.projection_id
            )
            projection = ProjectionPacket(
                projection_id=candidate.projection_id,
                snapshot=flow,
                assumptions=assumptions,
                comparison=comparison,
            )
            evidence[candidate.projection_id] = EvidenceRecord(
                evidence_id=candidate.projection_id,
                kind=EvidenceKind.PROJECTION,
                airport_icao=opening.airport_icao,
                snapshot_id=row["snapshot_id"]
                if row["snapshot_id"] == snapshot["snapshot_id"]
                else OUTSIDE_SNAPSHOT,
                data_mode="live",  # derived from live evidence; never an observation
                fetched_at_utc=opening.created_at_utc,
            )

    weather: WeatherFinding | None = None
    finding = await _one(adapter, "agent_findings", "finding_id", ready.weather_assessment_id)
    if finding is not None:
        content = json.loads(finding["content_json"])
        weather = WeatherFinding(
            assessment_id=content["finding_id"],
            case_id=content["envelope"]["case_id"],
            snapshot_id=finding["snapshot_id"],
            status=content["status"],
        )
    return ValidationPacket(
        opening=opening,
        ready=ready,
        candidate=candidate,
        evidence=evidence,
        weather=weather,
        projection=projection,
        now_utc=now,
        snapshot_cutoff_utc=utc(snapshot["cutoff_at_utc"]) if "cutoff_at_utc" in snapshot else None,
    )
