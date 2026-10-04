"""Independent verification of a candidate's projection and its projected numbers.

Uses the public deterministic projection functions; never trusts stored results.
A projection is a modeled scenario, so a hypothetical capacity is only a limitation,
while any number that does not match a recomputation is rejected.
"""

from __future__ import annotations

from collections.abc import Iterable

from agents.validation.packet import Finding, Severity, ValidationPacket
from projection.aggregate_flow import (
    ProjectionComparison,
    ProjectionStatus,
    compare,
    verify_branch,
)
from projection.assumptions import NO_RESPONSE, CapacityBasis
from shared.contracts import FlowCandidate, MetricBasis

# Projected metric name -> (unit, value read from a computed comparison).
PROJECTED_METRIC_UNITS = {
    "projected_peak_runway_queue_baseline": "aircraft",
    "projected_peak_runway_queue_proposal": "aircraft",
    "projected_total_waiting_change": "aircraft-minutes",
}


def projected_values(comparison: ProjectionComparison) -> dict[str, int]:
    assert comparison.baseline and comparison.proposal and comparison.deltas
    return {
        "projected_peak_runway_queue_baseline": comparison.baseline.metrics.peak_runway_queue,
        "projected_peak_runway_queue_proposal": comparison.proposal.metrics.peak_runway_queue,
        "projected_total_waiting_change": comparison.deltas.total_waiting_aircraft_minutes,
    }


def check_projection(packet: ValidationPacket) -> tuple[Finding, ...]:
    candidate = packet.candidate
    if candidate is None:
        return ()
    if candidate.projection_id is None:
        if any(m.basis is MetricBasis.PROJECTED for m in candidate.traffic_metrics):
            return (
                Finding(
                    "projected_metric_without_projection",
                    Severity.INTEGRITY,
                    "candidate reports projected values without a projection",
                ),
            )
        return ()
    projection = packet.projection
    if projection is None:
        return (Finding("projection_missing", Severity.MISSING, "stored projection not found"),)
    if projection.projection_id != candidate.projection_id:
        return (
            Finding(
                "projection_reference_mismatch",
                Severity.INTEGRITY,
                "loaded projection is not the one the candidate cites",
            ),
        )
    return tuple(_check_loaded_projection(packet, candidate))


def _check_loaded_projection(
    packet: ValidationPacket, candidate: FlowCandidate
) -> Iterable[Finding]:
    projection = packet.projection
    assert projection is not None
    comparison = projection.comparison
    snapshot_id = packet.opening.snapshot_id
    if snapshot_id != projection.snapshot.snapshot_id or snapshot_id != comparison.snapshot_id:
        yield Finding(
            "projection_snapshot_mismatch",
            Severity.INTEGRITY,
            "projection was computed from another snapshot",
        )
        return
    if comparison.status is not ProjectionStatus.COMPUTED or not (
        comparison.baseline and comparison.proposal and comparison.deltas
    ):
        yield Finding(
            "projection_not_computed",
            Severity.INTEGRITY,
            "candidate cites a projection that has no computed result",
        )
        return
    if comparison.baseline.response != NO_RESPONSE:
        yield Finding(
            "baseline_altered", Severity.INTEGRITY, "baseline must have no response applied"
        )
    try:
        recomputed = compare(
            projection.snapshot, projection.assumptions, comparison.proposal.response
        )
    except ValueError as error:
        yield Finding("projection_inputs_invalid", Severity.INTEGRITY, str(error))
        return
    if recomputed != comparison:
        yield Finding(
            "projection_not_reproducible",
            Severity.INTEGRITY,
            "recomputing from the stored inputs gives a different result",
        )
    for label, branch in (("baseline", comparison.baseline), ("proposal", comparison.proposal)):
        for problem in verify_branch(projection.snapshot, projection.assumptions, branch):
            yield Finding("projection_invariant", Severity.INTEGRITY, f"{label}: {problem}")

    yield from _check_projected_metrics(candidate, recomputed)
    yield from _check_parameters_match(candidate, recomputed)

    capacity = recomputed.capacity
    if capacity is not None and capacity.basis is CapacityBasis.HYPOTHETICAL:
        yield Finding(
            "hypothetical_capacity",
            Severity.LIMITATION,
            f"capacity scenario '{capacity.name}' is hypothetical, not an airport rate",
        )
    if recomputed.simplified_instant_transfer:
        yield Finding(
            "simplified_transfer",
            Severity.LIMITATION,
            "simplified model: released aircraft reach the runway queue instantly",
        )


def _check_projected_metrics(
    candidate: FlowCandidate, comparison: ProjectionComparison
) -> Iterable[Finding]:
    expected = projected_values(comparison)
    for metric in candidate.traffic_metrics:
        if metric.basis is not MetricBasis.PROJECTED:
            continue
        if metric.name not in expected:
            yield Finding(
                "unknown_projected_metric",
                Severity.INTEGRITY,
                f"{metric.name} cannot be checked against the projection",
            )
        elif metric.unit != PROJECTED_METRIC_UNITS[metric.name]:
            yield Finding("projected_unit", Severity.INTEGRITY, f"{metric.name} has wrong unit")
        elif metric.value != expected[metric.name]:
            yield Finding(
                "projected_value_mismatch",
                Severity.INTEGRITY,
                f"{metric.name} does not match the recomputed projection",
            )


def _check_parameters_match(
    candidate: FlowCandidate, comparison: ProjectionComparison
) -> Iterable[Finding]:
    """Proposed parameters must be exactly the projection's declared response."""
    assert comparison.proposal is not None
    response = comparison.proposal.response
    projected = {
        "release_cap": response.release_cap_per_hour,
        "window_start_minute": response.window_start_minute,
        "window_end_minute": response.window_end_minute,
    }
    for parameter in candidate.proposed_action_parameters:
        if parameter.name in projected and parameter.value != projected[parameter.name]:
            yield Finding(
                "parameters_differ_from_projection",
                Severity.INTEGRITY,
                f"{parameter.name} differs from the projected response",
            )
