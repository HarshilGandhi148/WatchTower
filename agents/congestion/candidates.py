"""Bounded FlowCandidate creation from fixed inputs, with evidence and limitations.

Pure and deterministic: the caller supplies every input, including ``now_utc``. The
decision rules are explicit and use configurable review thresholds that are demo
configuration, not operational limits; they are recorded in every candidate's
assumptions. Rules, in order:

1. no usable evidence at all -> insufficient_data (names what is missing);
2. supported demand pressure and a proposal that lowers the projected runway-queue peak
   -> change_worth_reviewing (weather-flow review if weather reports an impact, else a
   departure-release review);
3. traffic pressure, a published KDTW FAA event, or a weather impact without demand
   -> information_only notice (never an unsupported flow action);
4. otherwise usable evidence with nothing notable -> no_change.

Clear weather never blocks a candidate. DOWNSTREAM_RESTRICTION is never produced: it
needs a reliable flight-to-destination association, which no adapter provides yet.
Only the declared response parameters of the supplied projection are proposed; nothing
here invents a rate, a capacity or an affected flight.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum

from agents.congestion.metrics import DemandWindows, NearbyTraffic, traffic_change
from data.faa_status import FaaEvent, FaaStatusSnapshot
from projection.aggregate_flow import (
    FlowSnapshot,
    ProjectionComparison,
    ProjectionStatus,
    apportion,
    compare,
)
from projection.assumptions import (
    CapacityBasis,
    ProjectionAssumptions,
    ResponseParameters,
)
from shared.contracts import (
    ActionParameter,
    ActionType,
    AdvisoryType,
    CandidateStatus,
    EvidenceQuality,
    FlowCandidate,
    MessageEnvelope,
    MetricBasis,
    TrafficMetric,
)

KDTW = "KDTW"
_QUALITY_RANK = {
    EvidenceQuality.ADEQUATE: 2,
    EvidenceQuality.LIMITED: 1,
    EvidenceQuality.UNAVAILABLE: 0,
}


class WeatherSignal(StrEnum):
    """What the Weather Agent reported for this case, reduced to what congestion needs."""

    NO_MATERIAL_IMPACT = "no_material_impact"
    IMPACT = "impact"
    UNKNOWN = "unknown"  # no assessment, timeout, or insufficient data


@dataclass(frozen=True, slots=True)
class ReviewThresholds:
    """Demo configuration, recorded in the candidate; not operational limits."""

    peak_runway_queue_aircraft: int = 5
    nearby_notice_aircraft: int = 25
    nearby_increase_aircraft: int = 5

    def __post_init__(self) -> None:
        for value in (
            self.peak_runway_queue_aircraft,
            self.nearby_notice_aircraft,
            self.nearby_increase_aircraft,
        ):
            if type(value) is not int or value < 1:
                raise ValueError("thresholds must be positive integers")


@dataclass(frozen=True, slots=True)
class EvidenceRefs:
    """Stored-record IDs for the inputs; a used input without an ID is a caller error."""

    traffic: str | None = None
    demand: str | None = None
    projection_id: str | None = None
    faa_events: tuple[tuple[str, str], ...] = ()  # (FaaEvent.event_key, evidence id)


@dataclass(frozen=True, slots=True)
class CandidateInputs:
    envelope: MessageEnvelope  # evidence_ids are filled in from the inputs actually used
    now_utc: datetime
    horizon_minutes: int
    nearby: NearbyTraffic | None
    demand: DemandWindows
    evidence: EvidenceRefs
    weather_signal: WeatherSignal = WeatherSignal.UNKNOWN
    weather_assessment_id: str | None = None
    previous_nearby: NearbyTraffic | None = None
    faa: FaaStatusSnapshot | None = None
    comparison: ProjectionComparison | None = None
    thresholds: ReviewThresholds = ReviewThresholds()


def flow_snapshot_from_demand(
    snapshot_id: str,
    as_of_utc: datetime,
    windows: DemandWindows,
    initial_upstream_waiting: int | None,
    initial_runway_queue: int | None,
) -> FlowSnapshot:
    """Projection input from confirmed departures only, spread evenly within each window.

    Inferred departures are excluded. The result is an eligibility estimate from flight
    records, not observed gate readiness; unknown demand or backlog stays ``None``.
    """
    if windows.windows is None:
        return FlowSnapshot(
            snapshot_id, as_of_utc, initial_upstream_waiting, initial_runway_queue, None
        )
    per_minute: list[int] = []
    for window in windows.windows:
        per_minute.extend(apportion(window.departures_confirmed, (1, 1, 1, 1, 1)))
    return FlowSnapshot(
        snapshot_id,
        as_of_utc,
        initial_upstream_waiting,
        initial_runway_queue,
        tuple(per_minute),
        "estimated eligibility from confirmed departure records, not observed gate readiness",
    )


def project(
    snapshot: FlowSnapshot, assumptions: ProjectionAssumptions, proposal: ResponseParameters
) -> ProjectionComparison:
    """Call the deterministic projection; baseline and proposal share every input."""
    return compare(snapshot, assumptions, proposal)


def build_candidate(inputs: CandidateInputs) -> FlowCandidate:
    """Create the single FlowCandidate for this case from fixed inputs."""
    _check_inputs(inputs)
    nearby_known = inputs.nearby is not None and inputs.nearby.total is not None
    demand_known = inputs.demand.windows is not None
    comparison = inputs.comparison
    projected = comparison is not None and comparison.status is ProjectionStatus.COMPUTED
    faa_events = _kdtw_events(inputs.faa)
    thresholds = inputs.thresholds

    helpful = False
    pressure = False
    if projected:
        assert comparison and comparison.baseline and comparison.proposal and comparison.deltas
        pressure = comparison.baseline.metrics.peak_runway_queue >= (
            thresholds.peak_runway_queue_aircraft
        )
        helpful = (
            pressure
            and comparison.proposal.response.is_restriction
            and (comparison.deltas.peak_runway_queue < 0)
        )
    nearby_pressure = nearby_known and _nearby_notable(inputs)
    weather_impact = inputs.weather_signal is WeatherSignal.IMPACT

    if helpful:
        status = CandidateStatus.CHANGE_WORTH_REVIEWING
        issue = (
            AdvisoryType.WEATHER_FLOW_REVIEW
            if weather_impact
            else AdvisoryType.DEPARTURE_RELEASE_REVIEW
        )
        action: ActionType | None = (
            ActionType.REVIEW_FLOW_WINDOW if weather_impact else ActionType.REVIEW_RELEASE_RATE
        )
    elif pressure or nearby_pressure or faa_events or (weather_impact and nearby_known):
        status = CandidateStatus.INFORMATION_ONLY
        issue = AdvisoryType.TRAFFIC_PRESSURE_NOTICE
        action = ActionType.INFORMATION_NOTICE
    elif nearby_known or demand_known:
        status = CandidateStatus.NO_CHANGE
        issue = AdvisoryType.TRAFFIC_PRESSURE_NOTICE
        action = None
    else:
        status = CandidateStatus.INSUFFICIENT_DATA
        issue = AdvisoryType.TRAFFIC_PRESSURE_NOTICE
        action = None

    metrics = _metrics(inputs, projected)
    used = _evidence_used(inputs, nearby_known, demand_known, projected, faa_events)
    envelope = replace(inputs.envelope, evidence_ids=used)
    parameters = _parameters(inputs, helpful, weather_impact)
    return FlowCandidate(
        envelope=envelope,
        status=status,
        issue_type=issue,
        resource="KDTW departures" if issue is AdvisoryType.DEPARTURE_RELEASE_REVIEW else "KDTW",
        window_start_utc=inputs.now_utc,
        window_end_utc=inputs.now_utc + timedelta(minutes=inputs.horizon_minutes),
        quality=_quality(inputs, status, nearby_known, demand_known),
        traffic_metrics=metrics,
        weather_assessment_id=inputs.weather_assessment_id,
        faa_event_ids=tuple(_faa_evidence(inputs, event) for event in faa_events),
        proposed_action_type=action,
        proposed_action_parameters=parameters,
        projection_id=inputs.evidence.projection_id if projected else None,
        tradeoffs=_tradeoffs(comparison) if helpful else (),
        assumptions=_assumptions(inputs, projected),
        required_missing_inputs=_missing(inputs, nearby_known, demand_known),
    )


def _check_inputs(inputs: CandidateInputs) -> None:
    if inputs.now_utc.utcoffset() != timedelta(0):
        raise ValueError("now_utc must be an aware UTC datetime")
    if inputs.horizon_minutes <= 0:
        raise ValueError("horizon_minutes must be positive")
    refs = inputs.evidence
    if inputs.nearby is not None and inputs.nearby.total is not None and not refs.traffic:
        raise ValueError("nearby traffic is used but has no evidence id")
    if inputs.demand.windows is not None and not refs.demand:
        raise ValueError("demand is used but has no evidence id")
    comparison = inputs.comparison
    if comparison is not None and comparison.status is ProjectionStatus.COMPUTED:
        if not refs.projection_id:
            raise ValueError("a computed projection needs a projection_id")
        if comparison.snapshot_id != inputs.envelope.snapshot_id:
            raise ValueError("projection snapshot does not match the case snapshot")
    if inputs.weather_signal is WeatherSignal.IMPACT and not inputs.weather_assessment_id:
        raise ValueError("a weather impact needs the weather assessment id")


def _kdtw_events(faa: FaaStatusSnapshot | None) -> tuple[FaaEvent, ...]:
    return () if faa is None else faa.events_for_airport(KDTW)


def _faa_evidence(inputs: CandidateInputs, event: FaaEvent) -> str:
    mapping = dict(inputs.evidence.faa_events)
    if event.event_key not in mapping:
        raise ValueError(f"FAA event {event.event_key} has no evidence id")
    return mapping[event.event_key]


def _nearby_notable(inputs: CandidateInputs) -> bool:
    assert inputs.nearby is not None and inputs.nearby.total is not None
    if inputs.nearby.total >= inputs.thresholds.nearby_notice_aircraft:
        return True
    change = traffic_change(inputs.nearby, inputs.previous_nearby)
    return change is not None and change >= inputs.thresholds.nearby_increase_aircraft


def _metrics(inputs: CandidateInputs, projected: bool) -> tuple[TrafficMetric, ...]:
    out: list[TrafficMetric] = []
    refs = inputs.evidence
    if inputs.nearby is not None and refs.traffic:
        out.extend(inputs.nearby.metrics(refs.traffic))
        change = traffic_change(inputs.nearby, inputs.previous_nearby)
        if change is not None:
            out.append(
                TrafficMetric(
                    "nearby_aircraft_change",
                    change,
                    "aircraft",
                    MetricBasis.OBSERVED,
                    (refs.traffic,),
                )  # fmt: skip
            )
    if refs.demand:
        out.extend(inputs.demand.metrics(refs.demand))
    comparison = inputs.comparison
    if projected and comparison and comparison.baseline and comparison.proposal:
        assert comparison.deltas and refs.projection_id
        ev = (refs.projection_id,)
        b, p = comparison.baseline.metrics, comparison.proposal.metrics
        for name, value, unit in (
            ("projected_peak_runway_queue_baseline", b.peak_runway_queue, "aircraft"),
            ("projected_peak_runway_queue_proposal", p.peak_runway_queue, "aircraft"),
            (
                "projected_total_waiting_change",
                comparison.deltas.total_waiting_aircraft_minutes,
                "aircraft-minutes",
            ),
        ):
            out.append(TrafficMetric(name, value, unit, MetricBasis.PROJECTED, ev))
    return tuple(out)


def _evidence_used(
    inputs: CandidateInputs,
    nearby_known: bool,
    demand_known: bool,
    projected: bool,
    faa_events: tuple[FaaEvent, ...],
) -> tuple[str, ...]:
    refs = inputs.evidence
    ordered: list[str] = []
    if nearby_known and refs.traffic:
        ordered.append(refs.traffic)
    if demand_known and refs.demand:
        ordered.append(refs.demand)
    if projected and refs.projection_id:
        ordered.append(refs.projection_id)
    ordered.extend(_faa_evidence(inputs, event) for event in faa_events)
    return tuple(dict.fromkeys(ordered))


def _parameters(
    inputs: CandidateInputs, helpful: bool, weather_impact: bool
) -> tuple[ActionParameter, ...]:
    if not helpful:
        return ()
    comparison = inputs.comparison
    assert comparison and comparison.proposal
    response = comparison.proposal.response
    unit = "minutes after snapshot"
    window = (
        ActionParameter("window_start_minute", response.window_start_minute, unit),
        ActionParameter("window_end_minute", response.window_end_minute, unit),
    )
    if weather_impact or response.release_cap_per_hour is None:
        return window
    return (ActionParameter("release_cap", response.release_cap_per_hour, "aircraft/hour"), *window)


def _tradeoffs(comparison: ProjectionComparison | None) -> tuple[str, ...]:
    assert comparison and comparison.deltas
    d = comparison.deltas
    total = d.total_waiting_aircraft_minutes
    verdict = (
        "no reduction in total waiting"
        if total >= 0
        else f"total waiting down {-total} aircraft-minutes"
    )
    return (
        f"Projected peak runway queue changes by {d.peak_runway_queue:+d} aircraft.",
        f"Runway waiting {d.runway_waiting_aircraft_minutes:+d} and upstream waiting "
        f"{d.upstream_waiting_aircraft_minutes:+d} aircraft-minutes; {verdict}.",
    )


def _assumptions(inputs: CandidateInputs, projected: bool) -> tuple[str, ...]:
    t = inputs.thresholds
    notes = [
        f"review thresholds are demo configuration: peak runway queue >= "
        f"{t.peak_runway_queue_aircraft}, nearby >= {t.nearby_notice_aircraft} or "
        f"+{t.nearby_increase_aircraft}",
        "aircraft near KDTW are not assumed to be KDTW arrivals or departures",
    ]
    notes.extend(inputs.demand.assumptions)
    comparison = inputs.comparison
    if projected and comparison:
        if comparison.capacity is not None:
            c = comparison.capacity
            notes.append(
                f"capacity scenario '{c.name}': {c.effective_capacity_per_hour}/hour ({c.basis})"
            )
        notes.append(f"projection model {comparison.model_version}; illustrative, not predictive")
        notes.append(f"demand basis: {comparison.demand_basis}")
        if comparison.simplified_instant_transfer:
            notes.append("simplified model: released aircraft reach the runway queue instantly")
    return tuple(notes)


def _missing(inputs: CandidateInputs, nearby_known: bool, demand_known: bool) -> tuple[str, ...]:
    missing: list[str] = []
    if not nearby_known:
        reason = inputs.nearby.notes[0] if inputs.nearby and inputs.nearby.notes else "unavailable"
        missing.append(f"nearby traffic ({reason})")
    if not demand_known:
        reason = inputs.demand.unavailable_reason or "unavailable"
        missing.append(f"airport-associated demand ({reason})")
    if inputs.faa is None:
        missing.append("FAA NAS status (unavailable; absence of events is not established)")
    if inputs.weather_signal is WeatherSignal.UNKNOWN:
        missing.append("weather assessment")
    comparison = inputs.comparison
    if comparison is not None and comparison.status is ProjectionStatus.UNAVAILABLE:
        missing.extend(f"projection: {reason}" for reason in comparison.unavailable_reasons)
    return tuple(missing)


def _worse(*qualities: EvidenceQuality) -> EvidenceQuality:
    return min(qualities, key=_QUALITY_RANK.__getitem__)


def _quality(
    inputs: CandidateInputs, status: CandidateStatus, nearby_known: bool, demand_known: bool
) -> EvidenceQuality:
    if status is CandidateStatus.INSUFFICIENT_DATA:
        return EvidenceQuality.UNAVAILABLE
    parts = [EvidenceQuality.ADEQUATE]
    if nearby_known:
        assert inputs.nearby is not None
        parts.append(inputs.nearby.quality)
    if demand_known:
        parts.append(inputs.demand.quality)
    if status is CandidateStatus.CHANGE_WORTH_REVIEWING:
        comparison = inputs.comparison
        assert comparison is not None
        if comparison.capacity is None or comparison.capacity.basis is CapacityBasis.HYPOTHETICAL:
            parts.append(EvidenceQuality.LIMITED)
        if inputs.weather_signal is WeatherSignal.UNKNOWN:
            parts.append(EvidenceQuality.LIMITED)
    if not (nearby_known or demand_known):
        parts.append(EvidenceQuality.LIMITED)  # FAA-only or weather-only context
    return _worse(*parts)
