"""In-memory TEST double of the stored records the validator reads; no database involved.

Builds one consistent stored case: the partner's TEST snapshot plus a stored flight batch,
a weather finding, a projection payload and a projected congestion candidate. Rows use the
column names and JSON encodings of the real tables.
"""

import json
from collections import defaultdict
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any

from projection.aggregate_flow import FlowSnapshot, compare
from projection.assumptions import (
    CapacityBasis,
    CapacityScenario,
    ProjectionAssumptions,
    ResponseParameters,
)
from shared.contracts import (
    ActionParameter,
    ActionType,
    AdvisoryType,
    AgentName,
    CandidateStatus,
    EvidenceQuality,
    FlowCandidate,
    MessageEnvelope,
    MetricBasis,
    TrafficMetric,
)
from shared.orchestration import AssessmentRequest, CandidateReady
from shared.persistence import WeatherFinding, candidate_from_dict, storage_json
from shared.spacetimedb_adapter import SpacetimeError
from tests.fixtures.spacetimedb.congestion import snapshot_fixture

SNAPSHOT_ID = "TEST-snapshot"
CASE_ID = "TEST-case"
FLIGHT_BATCH = "TEST-flightaware-batch"
ADSB_BATCH = "TEST-adsb_lol-batch"
PROJECTION_ID = f"proj-{SNAPSHOT_ID}"
WEATHER_ID = "TEST-weather-finding"
CANDIDATE_ID = "TEST-candidate-1"
KEYS = {
    "evidence_snapshots": "snapshot_id",
    "evidence_records": "evidence_id",
    "agent_findings": "finding_id",
    "flow_candidates": "candidate_id",
    "pending_deliveries": "candidate_id",
    "projection_results": "projection_id",
}


def at_now() -> datetime:
    return datetime.now(UTC).replace(microsecond=0)


class FakeStore:
    """Async stand-in for SpacetimeAdapter's read/write calls used by validation."""

    def __init__(self) -> None:
        self.tables: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
        self.calls: list[tuple[str, object]] = []

    def put(self, table: str, row: dict[str, Any]) -> None:
        self.tables[table][row[KEYS[table]]] = row

    async def read_records(
        self, table: str, *, key: str | None = None, value: str | None = None, **_: object
    ) -> list[dict[str, Any]]:
        if key is None:
            return list(self.tables[table].values())
        row = self.tables[table].get(value or "")
        return [row] if row is not None else []

    async def load_record(self, table: str, key: str, value: str) -> dict[str, Any]:
        rows = await self.read_records(table, key=key, value=value)
        if len(rows) != 1:
            raise SpacetimeError("missing stored record")
        return rows[0]

    async def load_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        row = await self.load_record("evidence_snapshots", "snapshot_id", snapshot_id)
        return dict(json.loads(row["content_json"]))

    async def load_candidate(self, candidate_id: str) -> FlowCandidate:
        row = await self.load_record("flow_candidates", "candidate_id", candidate_id)
        return candidate_from_dict(json.loads(row["content_json"]))

    async def load_projection(self, projection_id: str) -> dict[str, Any]:
        row = await self.load_record("projection_results", "projection_id", projection_id)
        return dict(json.loads(row["content_json"]))

    async def call(self, reducer: str, value: object | None = None) -> None:
        self.calls.append((reducer, value))

    async def register_assessment(self, request: AssessmentRequest) -> None:
        self.calls.append(("register_assessment_request", request))

    async def receipt(self, ready: CandidateReady) -> None:
        self.calls.append(("record_candidate_receipt", ready))


def stored_snapshot(at: datetime) -> dict[str, Any]:
    """The partner's TEST snapshot with one stored, fresh FlightAware batch added."""
    value = snapshot_fixture(at)
    batch = {
        "batch_id": FLIGHT_BATCH,
        "schema_version": 1,
        "data_mode": "live",
        "provider": "flightaware",
        "source_ref": "https://example.test/TEST-departures",
        "configuration": value["configuration"],
        "fetched_at_utc": at,
        "records": [
            {
                "flight_id": f"TEST-flight-{n}",
                "direction": "departure",
                "time_utc": at + timedelta(minutes=n),
                "time_kind": "estimated_runway",
                "association": "confirmed_record",
                "cancelled": False,
            }
            for n in range(15)
        ],
    }
    value["batches"].append({"batch_id": FLIGHT_BATCH, "payload": batch})
    value["health"].append({"provider": "flightaware", "status": "ok"})
    value["evidence_ids"].append(FLIGHT_BATCH)
    return dict(json.loads(storage_json(value)))


def opening(at: datetime, evidence_ids: tuple[str, ...]) -> MessageEnvelope:
    return MessageEnvelope(
        schema_version=1,
        message_id=f"{CASE_ID}:open",
        case_id=CASE_ID,
        sender_agent=AgentName.CONGESTION,
        created_at_utc=at,
        snapshot_id=SNAPSHOT_ID,
        evidence_ids=evidence_ids,
        airport_icao="KDTW",
        valid_until_utc=at + timedelta(seconds=120),
    )


def projection_payload(at: datetime) -> tuple[dict[str, Any], Any]:
    flow = FlowSnapshot(SNAPSHOT_ID, at, 6, 4, (1,) * 15, "TEST demand; TEST backlog")
    assumptions = ProjectionAssumptions(
        capacity=CapacityScenario("TEST capacity", 30, CapacityBasis.OPERATOR_CONFIGURED)
    )
    response = ResponseParameters(30, 0, 15)
    result = compare(flow, assumptions, response)
    payload = {
        "projection_id": PROJECTION_ID,
        "snapshot_id": SNAPSHOT_ID,
        "model_version": result.model_version,
        "evidence_ids": [FLIGHT_BATCH],
        "input": flow,
        "assumptions": assumptions,
        "response": response,
        "result": result,
    }
    return payload, result


def candidate_for(request: AssessmentRequest, at: datetime) -> tuple[FlowCandidate, CandidateReady]:
    """The projected release review congestion would store in reply to ``request``."""
    _, result = projection_payload(at)
    r = request.envelope
    envelope = MessageEnvelope(
        schema_version=1,
        message_id=CANDIDATE_ID,
        case_id=CASE_ID,
        sender_agent=AgentName.CONGESTION,
        created_at_utc=at,
        snapshot_id=SNAPSHOT_ID,
        evidence_ids=(ADSB_BATCH, FLIGHT_BATCH, PROJECTION_ID),
        airport_icao="KDTW",
        valid_until_utc=r.valid_until_utc,
        correlation_id=r.correlation_id,
        reply_to=r.message_id,
    )
    b, p = result.baseline.metrics, result.proposal.metrics
    candidate = FlowCandidate(
        envelope=envelope,
        status=CandidateStatus.CHANGE_WORTH_REVIEWING,
        issue_type=AdvisoryType.DEPARTURE_RELEASE_REVIEW,
        resource="KDTW departures",
        window_start_utc=at,
        window_end_utc=at + timedelta(minutes=15),
        quality=EvidenceQuality.LIMITED,
        traffic_metrics=(
            TrafficMetric(
                "nearby_aircraft_total", 1, "aircraft", MetricBasis.OBSERVED, (ADSB_BATCH,)
            ),
            TrafficMetric(
                "departure_demand_confirmed", 15, "aircraft", MetricBasis.ESTIMATED, (FLIGHT_BATCH,)
            ),
            TrafficMetric(
                "projected_peak_runway_queue_baseline",
                b.peak_runway_queue,
                "aircraft",
                MetricBasis.PROJECTED,
                (PROJECTION_ID,),
            ),
            TrafficMetric(
                "projected_peak_runway_queue_proposal",
                p.peak_runway_queue,
                "aircraft",
                MetricBasis.PROJECTED,
                (PROJECTION_ID,),
            ),
            TrafficMetric(
                "projected_total_waiting_change",
                result.deltas.total_waiting_aircraft_minutes,
                "aircraft-minutes",
                MetricBasis.PROJECTED,
                (PROJECTION_ID,),
            ),
        ),
        weather_assessment_id=WEATHER_ID,
        proposed_action_type=ActionType.REVIEW_RELEASE_RATE,
        proposed_action_parameters=(
            ActionParameter("release_cap", 30, "aircraft/hour"),
            ActionParameter("window_start_minute", 0, "minutes after snapshot"),
            ActionParameter("window_end_minute", 15, "minutes after snapshot"),
        ),
        projection_id=PROJECTION_ID,
        assumptions=("initial departure backlog is operator-configured, not observed",),
    )
    ready = CandidateReady(
        envelope=replace(envelope, evidence_ids=r.evidence_ids),
        candidate_id=CANDIDATE_ID,
        weather_assessment_id=WEATHER_ID,
        assessment_round=request.assessment_round,
        projection_id=PROJECTION_ID,
    )
    return candidate, ready


def weather_finding(at: datetime, status: str = "no_material_impact") -> WeatherFinding:
    envelope = MessageEnvelope(
        schema_version=1,
        message_id="TEST-weather-reply-1",
        case_id=CASE_ID,
        sender_agent=AgentName.WEATHER,
        created_at_utc=at,
        snapshot_id=SNAPSHOT_ID,
        evidence_ids=(),
        airport_icao="KDTW",
        valid_until_utc=at + timedelta(seconds=120),
    )
    return WeatherFinding(
        finding_id=WEATHER_ID,
        envelope=envelope,
        assessment_round=1,
        window_start_utc=at,
        window_end_utc=at + timedelta(minutes=15),
        status=status,
        quality=EvidenceQuality.ADEQUATE,
        summary="TEST weather double: no material impact",
    )


def stored_case(at: datetime) -> tuple[FakeStore, MessageEnvelope]:
    """A store holding the snapshot, its evidence, a weather finding and a projection."""
    store = FakeStore()
    snapshot = stored_snapshot(at)
    store.put(
        "evidence_snapshots",
        {"snapshot_id": SNAPSHOT_ID, "content_json": json.dumps(snapshot), "closed": False},
    )
    for evidence_id in snapshot["evidence_ids"]:
        store.put(
            "evidence_records",
            {"evidence_id": evidence_id, "provider": "test", "content_json": "{}"},
        )
    store.put(
        "agent_findings",
        {
            "finding_id": WEATHER_ID,
            "snapshot_id": SNAPSHOT_ID,
            "content_json": storage_json(weather_finding(at)),
        },
    )
    payload, _ = projection_payload(at)
    store.put(
        "projection_results",
        {
            "projection_id": PROJECTION_ID,
            "snapshot_id": SNAPSHOT_ID,
            "content_json": storage_json(payload),
        },
    )
    return store, opening(at, tuple(snapshot["evidence_ids"]))


def store_candidate(store: FakeStore, candidate: FlowCandidate, ready: CandidateReady) -> None:
    """What submit_flow_candidate saves: the candidate and its pending delivery."""
    store.put(
        "flow_candidates",
        {
            "candidate_id": ready.candidate_id,
            "snapshot_id": SNAPSHOT_ID,
            "content_json": storage_json(candidate),
        },
    )
    store.put(
        "pending_deliveries",
        {
            "candidate_id": ready.candidate_id,
            "content_json": storage_json(ready),
            "status": "pending",
        },
    )
