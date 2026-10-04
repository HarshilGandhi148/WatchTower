"""Synthetic TEST validation packets; never provider observations or live evidence.

Values are hand-chosen to exercise the validator. The projection is computed with
the real deterministic model from synthetic inputs so its numbers are consistent.
"""

from dataclasses import replace
from datetime import UTC, datetime, timedelta

from agents.validation.packet import (
    EvidenceKind,
    EvidenceRecord,
    ProjectionPacket,
    ValidationPacket,
    WeatherFinding,
)
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
from shared.orchestration import CandidateReady

SNAPSHOT_AT = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)
NOW = SNAPSHOT_AT + timedelta(seconds=10)
CASE_ID = "test-case-1"
SNAPSHOT_ID = "test-snapshot-1"
TRAFFIC_ID = "test-adsb-1"
DEMAND_ID = "test-flights-1"
PROJECTION_ID = "test-projection-1"
FAA_ID = "test-faa-1"
WEATHER_ID = "test-weather-assessment-1"
CASE_EVIDENCE = (TRAFFIC_ID, DEMAND_ID, FAA_ID)  # inputs; the projection is derived
RELEASE_CAP = 30


def opening() -> MessageEnvelope:
    return MessageEnvelope(
        schema_version=1,
        message_id="test-open-1",
        case_id=CASE_ID,
        sender_agent=AgentName.CONGESTION,
        created_at_utc=SNAPSHOT_AT,
        snapshot_id=SNAPSHOT_ID,
        evidence_ids=CASE_EVIDENCE,
        airport_icao="KDTW",
        valid_until_utc=SNAPSHOT_AT + timedelta(minutes=2),
    )


def projection(
    *,
    capacity_basis: CapacityBasis = CapacityBasis.OPERATOR_CONFIGURED,
    release_cap: int = RELEASE_CAP,
) -> ProjectionPacket:
    """Backlog 6 upstream + 4 queued, one departure per minute, 30/hour capacity."""
    snapshot = FlowSnapshot(SNAPSHOT_ID, SNAPSHOT_AT, 6, 4, (1,) * 15, "TEST demand estimate")
    assumptions = ProjectionAssumptions(
        capacity=CapacityScenario("TEST departure capacity", 30, capacity_basis)
    )
    comparison = compare(snapshot, assumptions, ResponseParameters(release_cap, 0, 15))
    return ProjectionPacket(
        projection_id=PROJECTION_ID,
        snapshot=snapshot,
        assumptions=assumptions,
        comparison=comparison,
    )


def _candidate_envelope(evidence_ids: tuple[str, ...]) -> MessageEnvelope:
    return MessageEnvelope(
        schema_version=1,
        message_id="test-candidate-1",
        case_id=CASE_ID,
        sender_agent=AgentName.CONGESTION,
        created_at_utc=SNAPSHOT_AT + timedelta(seconds=5),
        snapshot_id=SNAPSHOT_ID,
        evidence_ids=evidence_ids,
        airport_icao="KDTW",
        valid_until_utc=SNAPSHOT_AT + timedelta(seconds=60),
        correlation_id="test-open-1",
        reply_to="test-request-1",
    )


def actionable_candidate(packet: ProjectionPacket | None = None) -> FlowCandidate:
    """A departure release review whose projected numbers match ``projection()``."""
    comparison = (packet or projection()).comparison
    assert comparison.baseline and comparison.proposal and comparison.deltas
    return FlowCandidate(
        envelope=_candidate_envelope((TRAFFIC_ID, DEMAND_ID, PROJECTION_ID)),
        status=CandidateStatus.CHANGE_WORTH_REVIEWING,
        issue_type=AdvisoryType.DEPARTURE_RELEASE_REVIEW,
        resource="KDTW departures",
        window_start_utc=SNAPSHOT_AT,
        window_end_utc=SNAPSHOT_AT + timedelta(minutes=15),
        quality=EvidenceQuality.ADEQUATE,
        traffic_metrics=(
            TrafficMetric(
                "nearby_aircraft_total", 30, "aircraft", MetricBasis.OBSERVED, (TRAFFIC_ID,)
            ),
            TrafficMetric(
                "departure_demand_confirmed", 15, "aircraft", MetricBasis.ESTIMATED, (DEMAND_ID,)
            ),
            TrafficMetric(
                "projected_peak_runway_queue_baseline",
                comparison.baseline.metrics.peak_runway_queue,
                "aircraft",
                MetricBasis.PROJECTED,
                (PROJECTION_ID,),
            ),
            TrafficMetric(
                "projected_peak_runway_queue_proposal",
                comparison.proposal.metrics.peak_runway_queue,
                "aircraft",
                MetricBasis.PROJECTED,
                (PROJECTION_ID,),
            ),
            TrafficMetric(
                "projected_total_waiting_change",
                comparison.deltas.total_waiting_aircraft_minutes,
                "aircraft-minutes",
                MetricBasis.PROJECTED,
                (PROJECTION_ID,),
            ),
        ),
        weather_assessment_id=WEATHER_ID,
        proposed_action_type=ActionType.REVIEW_RELEASE_RATE,
        proposed_action_parameters=(
            ActionParameter("release_cap", RELEASE_CAP, "aircraft/hour"),
            ActionParameter("window_start_minute", 0, "minutes after snapshot"),
            ActionParameter("window_end_minute", 15, "minutes after snapshot"),
        ),
        projection_id=PROJECTION_ID,
        tradeoffs=("Upstream text from congestion; the validator rewrites tradeoffs.",),
        assumptions=("TEST capacity is operator configured",),
    )


def information_candidate() -> FlowCandidate:
    """Traffic pressure notice from nearby traffic and a published FAA event."""
    return FlowCandidate(
        envelope=_candidate_envelope((TRAFFIC_ID, FAA_ID)),
        status=CandidateStatus.INFORMATION_ONLY,
        issue_type=AdvisoryType.TRAFFIC_PRESSURE_NOTICE,
        resource="KDTW",
        window_start_utc=SNAPSHOT_AT,
        window_end_utc=SNAPSHOT_AT + timedelta(minutes=15),
        quality=EvidenceQuality.LIMITED,
        traffic_metrics=(
            TrafficMetric(
                "nearby_aircraft_total", 31, "aircraft", MetricBasis.OBSERVED, (TRAFFIC_ID,)
            ),
        ),
        weather_assessment_id=WEATHER_ID,
        faa_event_ids=(FAA_ID,),
        proposed_action_type=ActionType.INFORMATION_NOTICE,
        assumptions=("aircraft near KDTW are not assumed to be KDTW traffic",),
        required_missing_inputs=("airport-associated demand (TEST: not configured)",),
    )


def ready_for(candidate: FlowCandidate) -> CandidateReady:
    """The CandidateReady references congestion would send for ``candidate``."""
    return CandidateReady(
        envelope=replace(candidate.envelope, evidence_ids=CASE_EVIDENCE),
        candidate_id=candidate.envelope.message_id,
        weather_assessment_id=candidate.weather_assessment_id or WEATHER_ID,
        assessment_round=1,
        projection_id=candidate.projection_id,
    )


def record(evidence_id: str, kind: EvidenceKind, **changes: object) -> EvidenceRecord:
    base = EvidenceRecord(
        evidence_id=evidence_id,
        kind=kind,
        airport_icao="KDTW",
        snapshot_id=SNAPSHOT_ID,
        data_mode="live",
        fetched_at_utc=SNAPSHOT_AT - timedelta(seconds=5),
    )
    return replace(base, **changes)  # type: ignore[arg-type]


def evidence() -> dict[str, EvidenceRecord]:
    return {
        TRAFFIC_ID: record(TRAFFIC_ID, EvidenceKind.TRAFFIC),
        DEMAND_ID: record(DEMAND_ID, EvidenceKind.DEMAND),
        PROJECTION_ID: record(PROJECTION_ID, EvidenceKind.PROJECTION),
        FAA_ID: record(FAA_ID, EvidenceKind.FAA_EVENT),
    }


def weather(status: str = "no_material_impact") -> WeatherFinding:
    return WeatherFinding(
        assessment_id=WEATHER_ID, case_id=CASE_ID, snapshot_id=SNAPSHOT_ID, status=status
    )


def packet(
    candidate: FlowCandidate | None = None,
    *,
    projection_packet: ProjectionPacket | None = None,
    **changes: object,
) -> ValidationPacket:
    """A consistent packet; ``changes`` override any ValidationPacket field."""
    proj = projection_packet or projection()
    chosen = candidate or actionable_candidate(proj)
    base = ValidationPacket(
        opening=opening(),
        ready=ready_for(chosen),
        candidate=chosen,
        evidence=evidence(),
        weather=weather(),
        projection=proj if chosen.projection_id else None,
        now_utc=NOW,
    )
    return replace(base, **changes)  # type: ignore[arg-type]
