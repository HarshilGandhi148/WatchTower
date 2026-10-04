"""Congestion side of the validation-owned case protocol (docs/orchestration.md).

Pure and testable; agent.py only moves messages. One cycle = one fixed snapshot:

1. ``start_cycle`` -> CaseOpened envelope for validation (congestion opens the case).
2. ``on_assessment_request`` (round 1 or 2 from validation) -> weather request to send.
3. ``on_weather_reply`` applies the reply through CaseCoordinator.
4. ``advance`` -> CandidateReady references for validation, each round exactly once.

CandidateReady must name a stored weather assessment, so a case whose weather exchange
timed out or came back without an assessment id is not submitted; validation closes it
as insufficient data at its deadline. Prepared candidates stay retryable until
mark_persisted confirms the database commit.
The async persistence wrapper saves the candidate and delivery before notification.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta

from agents.congestion.candidates import (
    CandidateInputs,
    EvidenceRefs,
    ReviewThresholds,
    WeatherSignal,
    build_candidate,
)
from agents.congestion.coordination import (
    CaseCoordinator,
    CaseState,
    Outcome,
    ReplyResult,
)
from agents.congestion.feeds import FeedSnapshot
from agents.congestion.metrics import NearbyTraffic, demand_windows, nearby_traffic
from agents.congestion.projections import (
    PreparedProjection,
    ProjectionSettings,
    prepare_projection,
)
from shared.contracts import (
    AgentName,
    FlowCandidate,
    MessageEnvelope,
    WeatherAssessmentRequest,
)
from shared.orchestration import AssessmentRequest, CandidateReady

OPENING_VALIDITY = timedelta(minutes=10)
_STATUS_TO_SIGNAL = {
    "impact": WeatherSignal.IMPACT,
    "no_material_impact": WeatherSignal.NO_MATERIAL_IMPACT,
    "insufficient_data": WeatherSignal.UNKNOWN,
}

Builder = Callable[[MessageEnvelope, WeatherSignal, str | None], FlowCandidate]


class ProtocolError(ValueError):
    """A message from validation does not fit this case; it must be dropped."""


@dataclass(slots=True)
class _Case:
    opening: MessageEnvelope
    build: Builder
    request: AssessmentRequest | None = None  # validation's current round
    weather_requests: dict[str, WeatherAssessmentRequest] = field(default_factory=dict)
    submitted_rounds: set[int] = field(default_factory=set)
    abandoned: bool = False
    prepared: dict[int, tuple[FlowCandidate, CandidateReady]] = field(default_factory=dict)
    window_start: datetime | None = None
    window_end: datetime | None = None


@dataclass(frozen=True, slots=True)
class Advance:
    ready: tuple[CandidateReady, ...]
    abandoned: tuple[str, ...]  # case ids with no weather assessment to reference


class CongestionService:
    def __init__(
        self,
        coordinator: CaseCoordinator | None = None,
        *,
        horizon_minutes: int = 15,
        thresholds: ReviewThresholds | None = None,
        projection: ProjectionSettings | None = None,
    ) -> None:
        """``projection`` (operator capacity and backlog) enables stored-demand projections."""
        self._coordinator = coordinator or CaseCoordinator()
        self._projection = projection
        self._horizon = horizon_minutes
        self._thresholds = thresholds or ReviewThresholds()
        self._cases: dict[str, _Case] = {}
        self._previous_nearby: NearbyTraffic | None = None
        self.candidates: dict[
            str, FlowCandidate
        ] = {}  # prepared records; durable storage is required
        # Prepared projections by id; persisted before any candidate that cites them.
        self.projections: dict[str, PreparedProjection] = {}

    @property
    def open_cases(self) -> tuple[str, ...]:
        return tuple(self._cases)

    # -- 1. open ---------------------------------------------------------------------

    def start_cycle(self, feeds: FeedSnapshot, now: datetime) -> MessageEnvelope:
        """Fix one snapshot and return the CaseOpened envelope to send to validation."""
        if (
            not feeds.snapshot_id
            or not feeds.case_id
            or feeds.window_start_utc is None
            or feeds.window_end_utc is None
            or feeds.expires_at_utc is None
        ):
            raise ValueError("Congestion requires a stored evidence snapshot")
        case_id, snapshot_id = feeds.case_id, feeds.snapshot_id
        existing = self._cases.get(case_id)
        if existing is not None:
            if existing.opening.snapshot_id != snapshot_id:
                raise ProtocolError("case is bound to another snapshot")
            return existing.opening
        now = feeds.window_start_utc
        nearby = nearby_traffic(
            feeds.adsb,
            feeds.cutoff_at_utc or now,
            center=(feeds.configuration.latitude_deg, feeds.configuration.longitude_deg),
            radius_nm=feeds.configuration.radius_nm,
        )
        prepared = prepare_projection(feeds, self._projection)
        refs = EvidenceRefs(
            traffic=feeds.traffic_evidence_id,
            faa_events=feeds.faa_evidence_ids,
            demand=feeds.demand_evidence_id,
            projection_id=prepared.projection_id if prepared else None,
        )
        evidence = feeds.evidence_ids
        opening = MessageEnvelope(
            schema_version=MessageEnvelope.SUPPORTED_SCHEMA_VERSION,
            message_id=f"{case_id}:open",
            case_id=case_id,
            sender_agent=AgentName.CONGESTION,
            created_at_utc=now,
            snapshot_id=snapshot_id,
            evidence_ids=evidence,
            airport_icao="KDTW",
            valid_until_utc=feeds.expires_at_utc,
        )
        previous = self._previous_nearby
        horizon = int((feeds.window_end_utc - feeds.window_start_utc).total_seconds() / 60)
        thresholds = self._thresholds

        def build(
            envelope: MessageEnvelope, signal: WeatherSignal, assessment_id: str | None
        ) -> FlowCandidate:
            return build_candidate(
                CandidateInputs(
                    envelope=envelope,
                    now_utc=now,
                    horizon_minutes=horizon,
                    nearby=nearby,
                    # With an observed backlog, those flights are the initial queue and
                    # are excluded from demand, exactly as the projection used it.
                    demand=prepared.demand
                    if prepared
                    else demand_windows(
                        feeds.demand_records,
                        feeds.demand_fetched_at_utc,
                        now,
                        horizon,
                        evidence_time_utc=feeds.cutoff_at_utc,
                    ),
                    evidence=refs,
                    weather_signal=signal,
                    weather_assessment_id=assessment_id,
                    previous_nearby=previous,
                    faa=feeds.faa,
                    comparison=prepared.comparison if prepared else None,
                    thresholds=thresholds,
                    backlog_observed=prepared.backlog_observed if prepared else True,
                )
            )

        if prepared is not None:
            self.projections[prepared.projection_id] = prepared
        self._cases[case_id] = _Case(
            opening, build, window_start=feeds.window_start_utc, window_end=feeds.window_end_utc
        )
        self._previous_nearby = nearby
        return opening

    # -- 2. validation asks for an assessment ----------------------------------------

    def on_assessment_request(
        self, request: AssessmentRequest, now: datetime
    ) -> WeatherAssessmentRequest:
        """Handle round 1 or 2 from validation; returns the weather request to send.

        A retried request (same message id) returns the same weather request.
        """
        e = request.envelope
        case = self._cases.get(e.case_id)
        if case is None:
            raise ProtocolError("unknown case")
        if e.sender_agent is not AgentName.VALIDATION:
            raise ProtocolError("assessment requests come from validation")
        if e.snapshot_id != case.opening.snapshot_id or set(e.evidence_ids) != set(
            case.opening.evidence_ids
        ):
            raise ProtocolError("request is not for this case's snapshot")
        if e.valid_until_utc <= now:
            raise ProtocolError("request expired")
        if e.message_id in case.weather_requests:
            return case.weather_requests[e.message_id]

        if request.assessment_round == 1:
            if case.request is not None:
                raise ProtocolError("round 1 was already requested")
            weather = self._coordinator.open_case(
                e.case_id,
                e.snapshot_id,
                e.evidence_ids,
                now,
                case.window_start or case.opening.created_at_utc,
                case.window_end or (case.opening.created_at_utc + timedelta(minutes=self._horizon)),
                valid_until=e.valid_until_utc,
            )
        else:
            if case.request is None or 1 not in case.submitted_rounds:
                raise ProtocolError("round 2 before a round-1 candidate")
            result = self._coordinator.on_correction_request(e, now)
            if result.outcome is not Outcome.ACCEPTED or result.request is None:
                raise ProtocolError(f"reassessment refused: {result.reason}")
            weather = result.request
        case.request = request
        case.weather_requests[e.message_id] = weather
        return weather

    # -- 3. weather replies ------------------------------------------------------------

    def on_weather_reply(
        self,
        envelope: MessageEnvelope,
        status: str,
        assessment_id: str | None,
        now: datetime,
    ) -> ReplyResult:
        signal = _STATUS_TO_SIGNAL.get(status)
        if signal is None:
            raise ProtocolError(f"unknown weather status {status!r}")
        return self._coordinator.on_weather_reply(envelope, signal, assessment_id, now)

    # -- 4. hand candidates to validation ----------------------------------------------

    def advance(self, now: datetime) -> Advance:
        ready: list[CandidateReady] = []
        abandoned: list[str] = []
        for case_id, case in list(self._cases.items()):
            if case.request is None:
                if now >= case.opening.valid_until_utc:
                    del self._cases[case_id]  # validation never asked; forget it
                continue
            view = self._coordinator.poll(case_id, now)
            round_ = case.request.assessment_round
            if view.state is CaseState.CLOSED or now >= case.request.envelope.valid_until_utc:
                del self._cases[case_id]
                continue
            if (
                view.state not in (CaseState.READY, CaseState.SUBMITTED)
                or round_ in case.submitted_rounds
            ):
                continue
            if view.weather_assessment_id is None:
                if not case.abandoned:
                    case.abandoned = True
                    abandoned.append(case_id)
                continue
            prepared = case.prepared.get(round_)
            if prepared is None:
                candidate = self._coordinator.submit(case_id, case.build, now)
                candidate = replace(
                    candidate,
                    envelope=replace(
                        candidate.envelope,
                        valid_until_utc=min(
                            candidate.envelope.valid_until_utc,
                            case.request.envelope.valid_until_utc,
                        ),
                    ),
                )
                ready_message = self._candidate_ready(
                    case, candidate, view.weather_assessment_id, now
                )
                prepared = (candidate, ready_message)
                case.prepared[round_] = prepared
            candidate, ready_message = prepared
            self.candidates[candidate.envelope.message_id] = candidate
            ready.append(ready_message)
        return Advance(tuple(ready), tuple(abandoned))

    def mark_persisted(self, ready: CandidateReady) -> None:
        case = self._cases[ready.envelope.case_id]
        if case.prepared[ready.assessment_round][1] != ready:
            raise ProtocolError("persisted candidate differs from prepared candidate")
        case.submitted_rounds.add(ready.assessment_round)

    @staticmethod
    def _candidate_ready(
        case: _Case, candidate: FlowCandidate, weather_assessment_id: str, now: datetime
    ) -> CandidateReady:
        request = case.request
        assert request is not None
        r = request.envelope
        envelope = MessageEnvelope(
            schema_version=r.schema_version,
            message_id=candidate.envelope.message_id,  # validation's round 2 replies to this
            case_id=r.case_id,
            sender_agent=AgentName.CONGESTION,
            created_at_utc=now,
            snapshot_id=r.snapshot_id,
            evidence_ids=r.evidence_ids,  # the opening's evidence set, unchanged
            airport_icao=r.airport_icao,
            valid_until_utc=r.valid_until_utc,  # never beyond validation's deadline
            correlation_id=r.correlation_id,
            reply_to=r.message_id,
        )
        return CandidateReady(
            envelope=envelope,
            candidate_id=candidate.envelope.message_id,
            weather_assessment_id=weather_assessment_id,
            assessment_round=request.assessment_round,
            projection_id=candidate.projection_id,
        )
