"""Deterministic baseline-vs-proposal aggregate departure flow from a fixed snapshot.

Pure functions only: no provider, database, agent or clock access, and no
randomness. One-minute steps track separate upstream waiting, in-transit and
runway-queue counts so moving delay upstream is never reported as removing it
(plan section 8.3). Missing inputs give UNAVAILABLE results, never zeros.

Per minute ``t``: aircraft due from transit join the runway queue; demand becomes
available upstream; the response policy releases some of it (zero-delay releases
join the queue immediately, the rest enter transit); then up to the minute's
capacity is served from the queue.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from projection.assumptions import (
    NO_RESPONSE,
    CapacityScenario,
    ProjectionAssumptions,
    ResponseParameters,
    check_response,
    require_count,
)


class ProjectionStatus(StrEnum):
    COMPUTED = "computed"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class FlowSnapshot:
    """Immutable live-derived inputs. ``None`` means unknown, which is not zero."""

    snapshot_id: str
    as_of_utc: datetime
    initial_upstream_waiting: int | None
    initial_runway_queue: int | None
    demand_per_minute: tuple[int, ...] | None
    demand_basis: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot_id, str) or not self.snapshot_id.strip():
            raise ValueError("snapshot_id must be a non-empty string")
        if not isinstance(self.as_of_utc, datetime) or self.as_of_utc.utcoffset() != timedelta(0):
            raise ValueError("as_of_utc must be an aware UTC datetime")
        for name in ("initial_upstream_waiting", "initial_runway_queue"):
            value = getattr(self, name)
            if value is not None:
                require_count(value, name)
        if self.demand_per_minute is not None:
            if not isinstance(self.demand_per_minute, tuple):
                raise ValueError("demand_per_minute must be an immutable tuple")
            for count in self.demand_per_minute:
                require_count(count, "demand count")
            if not self.demand_basis.strip():
                raise ValueError("Demand needs a basis label (e.g. schedule estimate)")


@dataclass(frozen=True, slots=True)
class BranchSeries:
    """End-of-minute state for minutes 1..horizon (index 0 is the first minute)."""

    demand: tuple[int, ...]
    released: tuple[int, ...]
    served: tuple[int, ...]
    upstream_waiting: tuple[int, ...]
    runway_queue: tuple[int, ...]
    in_transit: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class BranchMetrics:
    total_demand: int
    total_released: int
    total_served: int
    final_upstream_waiting: int
    final_runway_queue: int
    final_in_transit: int
    upstream_waiting_aircraft_minutes: int
    runway_waiting_aircraft_minutes: int
    total_waiting_aircraft_minutes: int
    peak_runway_queue: int
    peak_runway_queue_minute: int


@dataclass(frozen=True, slots=True)
class BranchResult:
    response: ResponseParameters
    series: BranchSeries
    metrics: BranchMetrics


@dataclass(frozen=True, slots=True)
class ComparisonDeltas:
    """Proposal minus baseline. A negative waiting delta is an improvement."""

    total_waiting_aircraft_minutes: int
    runway_waiting_aircraft_minutes: int
    upstream_waiting_aircraft_minutes: int
    peak_runway_queue: int
    total_served: int


@dataclass(frozen=True, slots=True)
class ProjectionComparison:
    status: ProjectionStatus
    snapshot_id: str
    model_version: str
    input_fingerprint: str
    capacity: CapacityScenario | None
    demand_basis: str
    simplified_instant_transfer: bool
    unavailable_reasons: tuple[str, ...] = ()
    baseline: BranchResult | None = None
    proposal: BranchResult | None = None
    deltas: ComparisonDeltas | None = None


def minute_allocation(rate_per_hour: int, minute: int) -> int:
    """Integer share of an hourly rate for one minute; the cumulative sum is exact."""
    return (minute + 1) * rate_per_hour // 60 - minute * rate_per_hour // 60


def apportion(count: int, weights: tuple[int, ...]) -> tuple[int, ...]:
    """Split ``count`` by integer weights, largest remainder first, ties to lower index."""
    total = sum(weights)
    base = [count * w // total for w in weights]
    remainders = [count * w % total for w in weights]
    order = sorted(range(len(weights)), key=lambda i: (-remainders[i], i))
    for index in order[: count - sum(base)]:
        base[index] += 1
    return tuple(base)


def input_fingerprint(
    snapshot: FlowSnapshot, assumptions: ProjectionAssumptions, response: ResponseParameters
) -> str:
    """Stable hash of every input, so identical inputs can be recognised as such."""
    payload: dict[str, Any] = {
        "snapshot": asdict(snapshot),
        "assumptions": asdict(assumptions),
        "response": asdict(response),
    }
    text = json.dumps(payload, sort_keys=True, default=lambda v: v.isoformat())
    return hashlib.sha256(text.encode()).hexdigest()


def missing_inputs(snapshot: FlowSnapshot, assumptions: ProjectionAssumptions) -> tuple[str, ...]:
    reasons: list[str] = []
    if snapshot.demand_per_minute is None:
        reasons.append("demand is unavailable")
    elif len(snapshot.demand_per_minute) != assumptions.horizon_minutes:
        reasons.append("demand does not cover the projection horizon")
    if snapshot.initial_upstream_waiting is None:
        reasons.append("initial upstream waiting count is unknown")
    if snapshot.initial_runway_queue is None:
        reasons.append("initial runway queue is unknown")
    if assumptions.capacity is None:
        reasons.append("capacity is not configured")
    return tuple(reasons)


def simulate_branch(
    snapshot: FlowSnapshot, assumptions: ProjectionAssumptions, response: ResponseParameters
) -> BranchResult:
    """Run one branch. Raises ValueError if required inputs are missing or out of bounds."""
    reasons = missing_inputs(snapshot, assumptions)
    if reasons:
        raise ValueError("; ".join(reasons))
    check_response(response, assumptions)
    assert snapshot.demand_per_minute is not None
    assert snapshot.initial_upstream_waiting is not None
    assert snapshot.initial_runway_queue is not None
    assert assumptions.capacity is not None

    horizon = assumptions.horizon_minutes
    weights = assumptions.transit_delay_weights
    capacity_rate = assumptions.capacity.effective_capacity_per_hour
    due = [0] * (horizon + len(weights) + 1)  # transit arrivals by minute index

    upstream = snapshot.initial_upstream_waiting
    queue = snapshot.initial_runway_queue
    released_s: list[int] = []
    served_s: list[int] = []
    upstream_s: list[int] = []
    queue_s: list[int] = []
    transit_s: list[int] = []

    for t in range(horizon):
        queue += due[t]
        available = upstream + snapshot.demand_per_minute[t]
        released = available
        if (
            response.release_cap_per_hour is not None
            and response.window_start_minute <= t < response.window_end_minute
        ):
            released = min(available, minute_allocation(response.release_cap_per_hour, t))
        upstream = available - released
        for delay, count in enumerate(apportion(released, weights)):
            if delay == 0:
                queue += count
            else:
                due[t + delay] += count
        served = min(queue, minute_allocation(capacity_rate, t))
        queue -= served

        released_s.append(released)
        served_s.append(served)
        upstream_s.append(upstream)
        queue_s.append(queue)
        transit_s.append(sum(due[t + 1 :]))

    series = BranchSeries(
        demand=snapshot.demand_per_minute,
        released=tuple(released_s),
        served=tuple(served_s),
        upstream_waiting=tuple(upstream_s),
        runway_queue=tuple(queue_s),
        in_transit=tuple(transit_s),
    )
    peak = max(queue_s)
    metrics = BranchMetrics(
        total_demand=sum(series.demand),
        total_released=sum(series.released),
        total_served=sum(series.served),
        final_upstream_waiting=upstream_s[-1],
        final_runway_queue=queue_s[-1],
        final_in_transit=transit_s[-1],
        upstream_waiting_aircraft_minutes=sum(upstream_s),
        runway_waiting_aircraft_minutes=sum(queue_s),
        total_waiting_aircraft_minutes=sum(upstream_s) + sum(queue_s),
        peak_runway_queue=peak,
        peak_runway_queue_minute=queue_s.index(peak) + 1,
    )
    return BranchResult(response=response, series=series, metrics=metrics)


def compare(
    snapshot: FlowSnapshot,
    assumptions: ProjectionAssumptions,
    proposal: ResponseParameters,
    baseline: ResponseParameters = NO_RESPONSE,
) -> ProjectionComparison:
    """Compare baseline and proposal on identical snapshot, model, demand and capacity."""
    fingerprint = input_fingerprint(snapshot, assumptions, proposal)
    common: dict[str, Any] = {
        "snapshot_id": snapshot.snapshot_id,
        "model_version": assumptions.model_version,
        "input_fingerprint": fingerprint,
        "capacity": assumptions.capacity,
        "demand_basis": snapshot.demand_basis,
        "simplified_instant_transfer": assumptions.simplified_instant_transfer,
    }
    reasons = missing_inputs(snapshot, assumptions)
    if reasons:
        return ProjectionComparison(
            status=ProjectionStatus.UNAVAILABLE, unavailable_reasons=reasons, **common
        )
    base = simulate_branch(snapshot, assumptions, baseline)
    prop = simulate_branch(snapshot, assumptions, proposal)
    b, p = base.metrics, prop.metrics
    deltas = ComparisonDeltas(
        total_waiting_aircraft_minutes=p.total_waiting_aircraft_minutes
        - b.total_waiting_aircraft_minutes,
        runway_waiting_aircraft_minutes=p.runway_waiting_aircraft_minutes
        - b.runway_waiting_aircraft_minutes,
        upstream_waiting_aircraft_minutes=p.upstream_waiting_aircraft_minutes
        - b.upstream_waiting_aircraft_minutes,
        peak_runway_queue=p.peak_runway_queue - b.peak_runway_queue,
        total_served=p.total_served - b.total_served,
    )
    return ProjectionComparison(
        status=ProjectionStatus.COMPUTED,
        baseline=base,
        proposal=prop,
        deltas=deltas,
        **common,
    )


def compare_sensitivity(
    snapshot: FlowSnapshot,
    assumptions: ProjectionAssumptions,
    proposal: ResponseParameters,
    capacities: dict[str, CapacityScenario],
) -> dict[str, ProjectionComparison]:
    """Run the same comparison under each labelled capacity (e.g. low/base/high).

    These are scenario ranges, not calibrated probabilities.
    """
    return {
        label: compare(snapshot, replace(assumptions, capacity=capacity), proposal)
        for label, capacity in capacities.items()
    }


def verify_branch(
    snapshot: FlowSnapshot, assumptions: ProjectionAssumptions, branch: BranchResult
) -> tuple[str, ...]:
    """Independently check a branch against the invariants; returns violations (empty = ok).

    Intended for the validator. It re-derives bounds from the series, not by rerunning
    the simulation.
    """
    problems: list[str] = []
    reasons = missing_inputs(snapshot, assumptions)
    if reasons:
        return tuple(f"cannot verify: {reason}" for reason in reasons)
    assert snapshot.demand_per_minute is not None
    assert snapshot.initial_upstream_waiting is not None
    assert snapshot.initial_runway_queue is not None
    assert assumptions.capacity is not None
    s = branch.series
    horizon = assumptions.horizon_minutes
    fields = (s.demand, s.released, s.served, s.upstream_waiting, s.runway_queue, s.in_transit)
    if any(len(values) != horizon for values in fields):
        return ("series length does not match the horizon",)
    if s.demand != snapshot.demand_per_minute:
        problems.append("branch demand differs from the snapshot")
    if any(type(v) is not int or v < 0 for values in fields for v in values):
        problems.append("counts must be nonnegative integers")

    rate = assumptions.capacity.effective_capacity_per_hour
    backlog = snapshot.initial_upstream_waiting + snapshot.initial_runway_queue
    prev_upstream = snapshot.initial_upstream_waiting
    cum_demand = cum_served = 0
    for t in range(horizon):
        cum_demand += s.demand[t]
        cum_served += s.served[t]
        if s.released[t] > prev_upstream + s.demand[t]:
            problems.append(f"minute {t + 1}: released more than available demand")
        if s.served[t] > minute_allocation(rate, t):
            problems.append(f"minute {t + 1}: served exceeds capacity")
        total = cum_served + s.upstream_waiting[t] + s.runway_queue[t] + s.in_transit[t]
        if backlog + cum_demand != total:
            problems.append(f"minute {t + 1}: conservation identity broken")
        prev_upstream = s.upstream_waiting[t]
    return tuple(problems)
