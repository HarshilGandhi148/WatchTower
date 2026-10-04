"""Deterministic advisory wording from validated values only (plan section 11).

Every number comes from a checked metric or a recomputed projection, never from
upstream free text, which is not interpolated. Phrasing asks for review; it never
says an action was executed or issues a clearance. An LLM paraphrase may be added
later only if it preserves these templates' meaning.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.validation.packet import ProjectionPacket
from projection.aggregate_flow import ProjectionStatus
from shared.contracts import AdvisoryType, CandidateStatus, FlowCandidate

PROJECTION_CAPTION = (
    "Illustrative projection from the live snapshot; actual traffic continues updating."
)


@dataclass(frozen=True, slots=True)
class Wording:
    title: str
    suggested_response: str
    reason: str
    tradeoffs: tuple[str, ...]


def _parameters(candidate: FlowCandidate) -> dict[str, int | float]:
    return {p.name: p.value for p in candidate.proposed_action_parameters}


def _metric(candidate: FlowCandidate, name: str) -> int | float | None:
    for metric in candidate.traffic_metrics:
        if metric.name == name:
            return metric.value
    return None


def _horizon(candidate: FlowCandidate) -> int:
    return int((candidate.window_end_utc - candidate.window_start_utc).total_seconds() // 60)


def _projection_sentence(projection: ProjectionPacket | None) -> str | None:
    if projection is None or projection.comparison.status is not ProjectionStatus.COMPUTED:
        return None
    comparison = projection.comparison
    assert comparison.baseline and comparison.proposal and comparison.capacity
    return (
        f"Projected peak runway queue is {comparison.baseline.metrics.peak_runway_queue} "
        f"aircraft without a response and {comparison.proposal.metrics.peak_runway_queue} "
        f"with the reviewed response, at a modeled capacity of "
        f"{comparison.capacity.effective_capacity_per_hour} aircraft/hour "
        f"({comparison.capacity.basis.value.replace('_', ' ')})."
    )


def tradeoffs(projection: ProjectionPacket | None) -> tuple[str, ...]:
    """Both runway and upstream waiting; moving delay upstream is not called a saving."""
    if projection is None or projection.comparison.status is not ProjectionStatus.COMPUTED:
        return ()
    deltas = projection.comparison.deltas
    assert deltas is not None
    lines = [
        f"Projected peak runway queue changes by {deltas.peak_runway_queue:+d} aircraft.",
        f"Runway waiting changes by {deltas.runway_waiting_aircraft_minutes:+d} and upstream "
        f"waiting by {deltas.upstream_waiting_aircraft_minutes:+d} aircraft-minutes; total "
        f"waiting changes by {deltas.total_waiting_aircraft_minutes:+d} aircraft-minutes.",
    ]
    if deltas.total_waiting_aircraft_minutes >= 0:
        lines.append("The reviewed response does not reduce total waiting.")
    return tuple(lines)


def compose(
    candidate: FlowCandidate, projection: ProjectionPacket | None, weather_status: str | None
) -> Wording:
    horizon = _horizon(candidate)
    params = _parameters(candidate)
    projected = _projection_sentence(projection)
    window = (
        f"from minute {params['window_start_minute']} to {params['window_end_minute']} "
        "after the snapshot"
        if "window_start_minute" in params and "window_end_minute" in params
        else f"over the next {horizon} minutes"
    )

    if candidate.status is CandidateStatus.INFORMATION_ONLY:
        return _notice(candidate, projected, weather_status, horizon, projection)
    if candidate.issue_type is AdvisoryType.DEPARTURE_RELEASE_REVIEW:
        title = f"Review departure release rate (next {horizon} min)"
        response = (
            f"Review an aggregate departure release cap of {params['release_cap']} "
            f"aircraft/hour {window}."
        )
        reason = projected or "Airport-associated departure demand merits a release review."
    elif candidate.issue_type is AdvisoryType.WEATHER_FLOW_REVIEW:
        title = f"Review weather-affected flow (next {horizon} min)"
        response = f"Review the flow window {window} and coordinate capacity assumptions."
        reason = "The weather assessment reports an impact during this window."
        if projected:
            reason = f"{reason} {projected}"
    elif candidate.issue_type is AdvisoryType.DOWNSTREAM_RESTRICTION:
        count = len(candidate.affected_flight_ids)
        title = "Coordinate releases under a published restriction"
        response = f"Coordinate releases for {count} associated flight(s) under the restriction."
        reason = (
            f"{len(candidate.faa_event_ids)} published FAA event(s) apply to {count} "
            "reliably associated flight(s)."
        )
    else:
        return _notice(candidate, projected, weather_status, horizon, projection)
    return Wording(title, response, reason, tradeoffs(projection))


def _notice(
    candidate: FlowCandidate,
    projected: str | None,
    weather_status: str | None,
    horizon: int,
    projection: ProjectionPacket | None,
) -> Wording:
    parts: list[str] = []
    nearby = _metric(candidate, "nearby_aircraft_total")
    if nearby is not None:
        parts.append(
            f"{nearby} aircraft observed near KDTW; they are not all KDTW arrivals or departures."
        )
    change = _metric(candidate, "nearby_aircraft_change")
    if change is not None and change > 0:
        parts.append(f"Nearby traffic increased by {change} aircraft since the previous cycle.")
    if candidate.faa_event_ids:
        parts.append(f"{len(candidate.faa_event_ids)} published FAA event(s) for KDTW.")
    if weather_status == "impact":
        parts.append("The weather assessment reports an impact.")
    if projected:
        parts.append(projected)
    reason = " ".join(parts) or "Current traffic conditions merit review."
    return Wording(
        title=f"Traffic pressure notice (next {horizon} min)",
        suggested_response=(
            "Review traffic pressure; current data do not support a numerical flow change."
        ),
        reason=reason,
        tradeoffs=tradeoffs(projection) if projected else (),
    )
