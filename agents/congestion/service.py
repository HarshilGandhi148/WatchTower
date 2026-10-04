"""Congestion side of the validation-owned case protocol (docs/orchestration.md).

Pure and testable; agent.py only moves messages. One cycle = one fixed snapshot:

1. ``start_cycle`` -> CaseOpened envelope for validation (congestion opens the case).
2. ``on_assessment_request`` (round 1 or 2 from validation) -> weather request to send.
3. ``on_weather_reply`` applies the reply through CaseCoordinator.
4. ``advance`` -> CandidateReady references for validation, each round exactly once.

CandidateReady must name a stored weather assessment, so a case whose weather exchange
timed out or came back without an assessment id is not submitted; validation closes it
as insufficient data at its deadline. Candidates are kept in memory under their id until
the SpacetimeDB adapter can store them; validation cannot load them across processes yet.
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
    flow_snapshot_from_demand,
    project,
)
from agents.congestion.coordination import (
    CaseCoordinator,
    CaseState,
    Outcome,
    ReplyResult,
)
from agents.congestion.feeds import FeedSnapshot
from agents.congestion.metrics import (
    DemandWindows,
    DepartureBacklog,
    NearbyTraffic,
    demand_windows,
    departure_backlog,
    nearby_traffic,
)
from projection.aggregate_flow import ProjectionComparison, ProjectionStatus
from projection.assumptions import ProjectionAssumptions, ResponseParameters
from shared.contracts import (
    AgentName,
    FlowCandidate,
    MessageEnvelope,
    WeatherAssessmentRequest,
)
from shared.orchestration import AssessmentRequest, CandidateReady

OPENING_VALIDITY = timedelta(seconds=120)
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
        projection: ProjectionAssumptions | None = None,
    ) -> None:
        """``projection`` (with an operator-configured capacity) enables projections."""
        if projection is not None and projection.horizon_minutes != horizon_minutes:
            raise ValueError("projection horizon must match the service horizon")
        self._coordinator = coordinator or CaseCoordinator()
        self._projection = projection
        self._horizon = horizon_minutes
        self._thresholds = thresholds or ReviewThresholds()
        self._cases: dict[str, _Case] = {}
        self._previous_nearby: NearbyTraffic | None = None
        self.candidates: dict[str, FlowCandidate] = {}  # placeholder store until the DB exists

    @property
    def open_cases(self) -> tuple[str, ...]:
        return tuple(self._cases)

    # -- 1. open ---------------------------------------------------------------------

    def start_cycle(self, feeds: FeedSnapshot, now: datetime) -> MessageEnvelope:
        """Fix one snapshot and return the CaseOpened envelope to send to validation."""
        stamp = now.strftime("%Y%m%dT%H%M%SZ")
        case_id, snapshot_id = f"case-{stamp}", f"snap-{stamp}"
        existing = self._cases.get(case_id)
        if existing is not None:
            return existing.opening
        nearby = nearby_traffic(feeds.adsb, now)
        backlog = departure_backlog(feeds.flights, feeds.flights_fetched_at_utc, now)
        demand = demand_windows(
            feeds.flights,
            feeds.flights_fetched_at_utc,
            now,
            self._horizon,
            exclude_flight_ids=backlog.flight_ids,
        )
        if demand.windows is not None:
            demand = replace(demand, assumptions=demand.assumptions + backlog.assumptions)
        comparison = self._project(snapshot_id, now, demand, backlog)
        # Placeholder evidence ids until records are stored through the DB adapter.
        traffic_id = f"adsb-{stamp}" if feeds.adsb is not None else None
        demand_id = f"flights-{stamp}" if demand.windows is not None else None
        computed = comparison is not None and comparison.status is ProjectionStatus.COMPUTED
        events = feeds.faa.events_for_airport("KDTW") if feeds.faa is not None else ()
        refs = EvidenceRefs(
            traffic=traffic_id,
            demand=demand_id,
            projection_id=f"proj-{stamp}" if computed else None,
            faa_events=tuple((e.event_key, f"faa-{e.event_key}") for e in events),
        )
        evidence = tuple(
            x for x in (traffic_id, demand_id, *(eid for _, eid in refs.faa_events)) if x
        )
        opening = MessageEnvelope(
            schema_version=MessageEnvelope.SUPPORTED_SCHEMA_VERSION,
            message_id=f"{case_id}:open",
            case_id=case_id,
            sender_agent=AgentName.CONGESTION,
            created_at_utc=now,
            snapshot_id=snapshot_id,
            evidence_ids=evidence,
            airport_icao="KDTW",
            valid_until_utc=now + OPENING_VALIDITY,
        )
        previous = self._previous_nearby
        horizon, thresholds = self._horizon, self._thresholds

        def build(
            envelope: MessageEnvelope, signal: WeatherSignal, assessment_id: str | None
        ) -> FlowCandidate:
            return build_candidate(
                CandidateInputs(
                    envelope=envelope,
                    now_utc=now,
                    horizon_minutes=horizon,
                    nearby=nearby,
                    demand=demand,
                    evidence=refs,
                    weather_signal=signal,
                    weather_assessment_id=assessment_id,
                    previous_nearby=previous,
                    faa=feeds.faa,
                    comparison=comparison,
                    thresholds=thresholds,
                )
            )

        self._cases[case_id] = _Case(opening, build)
        self._previous_nearby = nearby
        return opening

    def _project(
        self,
        snapshot_id: str,
        now: datetime,
        demand: DemandWindows,
        backlog: DepartureBacklog,
    ) -> ProjectionComparison | None:
        """Baseline vs releasing at the modeled capacity rate; None when not configured."""
        assumptions = self._projection
        if assumptions is None or assumptions.capacity is None:
            return None
        rate = assumptions.capacity.effective_capacity_per_hour
        if rate < assumptions.bounds.min_release_cap_per_hour:
            return None  # no allowable release-rate proposal at this capacity
        snapshot = flow_snapshot_from_demand(
            snapshot_id,
            now,
            demand,
            backlog.upstream_waiting,
            backlog.runway_queue,
        )
        return project(snapshot, assumptions, ResponseParameters(rate, 0, self._horizon))

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
                now,
                now + timedelta(minutes=self._horizon),
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
            if view.state is not CaseState.READY or round_ in case.submitted_rounds:
                continue
            if view.weather_assessment_id is None:
                if not case.abandoned:
                    case.abandoned = True
                    abandoned.append(case_id)
                continue
            candidate = self._coordinator.submit(case_id, case.build, now)
            self.candidates[candidate.envelope.message_id] = candidate
            case.submitted_rounds.add(round_)
            ready.append(self._candidate_ready(case, candidate, view.weather_assessment_id, now))
        return Advance(tuple(ready), tuple(abandoned))

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
