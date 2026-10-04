"""Projection prepared from one stored snapshot, in the shape the store_projection reducer accepts.

Pure: the caller supplies the stored feeds and operator settings. Demand comes only from
the snapshot's stored, fresh, confirmed departure records in five-minute buckets.

Initial backlog, in order of preference:

1. Observed from stored gate times (pushed back = runway queue; past scheduled gate-out
   = upstream). Those flights are excluded from future demand so none is counted twice.
2. Otherwise an explicit operator setting, labelled not observed; candidate quality is
   limited accordingly. Demand then includes every departure, as the reducer derives it.

Missing settings, backlog or demand give no projection, never zero-valued results. The
current reducer accepts only case 2; case 1 needs the gate-time change proposed in
docs/interfaces.md.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import timedelta
from typing import Any

from agents.congestion.candidates import flow_snapshot_from_demand, project
from agents.congestion.feeds import FeedSnapshot
from agents.congestion.metrics import DemandWindows, demand_windows, departure_backlog
from projection.aggregate_flow import FlowSnapshot, ProjectionComparison, ProjectionStatus
from projection.assumptions import (
    MAX_CAPACITY_PER_HOUR,
    SUPPORTED_HORIZONS_MINUTES,
    CapacityBasis,
    CapacityScenario,
    ProjectionAssumptions,
    ResponseBounds,
    ResponseParameters,
    require_count,
)

BACKLOG_NOTE = "initial departure backlog is operator-configured, not observed"
OBSERVED_BACKLOG_NOTE = "initial departure backlog from stored gate times; readiness not observed"


@dataclass(frozen=True, slots=True)
class ProjectionSettings:
    """Operator configuration; none of these values is observed or inferred.

    The backlog pair is a fallback for snapshots whose flight records carry no gate
    times; leave both ``None`` to project only from an observed backlog.
    """

    capacity_per_hour: int
    backlog_upstream: int | None = None
    backlog_runway_queue: int | None = None

    def __post_init__(self) -> None:
        require_count(self.capacity_per_hour, "capacity_per_hour")
        if (self.backlog_upstream is None) != (self.backlog_runway_queue is None):
            raise ValueError("backlog_upstream and backlog_runway_queue go together")
        if self.backlog_upstream is not None:
            require_count(self.backlog_upstream, "backlog_upstream")
            require_count(self.backlog_runway_queue, "backlog_runway_queue")
        minimum = ResponseBounds().min_release_cap_per_hour
        if not minimum <= self.capacity_per_hour <= MAX_CAPACITY_PER_HOUR:
            raise ValueError(
                f"capacity_per_hour must be {minimum}-{MAX_CAPACITY_PER_HOUR} aircraft/hour"
            )


@dataclass(frozen=True, slots=True)
class PreparedProjection:
    projection_id: str
    snapshot: FlowSnapshot
    assumptions: ProjectionAssumptions
    response: ResponseParameters
    comparison: ProjectionComparison
    evidence_id: str  # the stored departure-demand batch
    demand: DemandWindows  # the windows the projection used, for the candidate's metrics
    backlog_observed: bool

    def payload(self) -> dict[str, Any]:
        """Exactly the store_projection reducer fields; the adapter serializes dataclasses."""
        return {
            "projection_id": self.projection_id,
            "snapshot_id": self.snapshot.snapshot_id,
            "model_version": self.comparison.model_version,
            "evidence_ids": [self.evidence_id],
            "input": self.snapshot,
            "assumptions": self.assumptions,
            "response": self.response,
            "result": self.comparison,
        }


def projection_id(snapshot_id: str) -> str:
    return f"proj-{snapshot_id}"


def prepare_projection(
    feeds: FeedSnapshot, settings: ProjectionSettings | None
) -> PreparedProjection | None:
    """Baseline vs releasing at the configured capacity; ``None`` when not supported."""
    start, end = feeds.window_start_utc, feeds.window_end_utc
    if (
        settings is None
        or start is None
        or end is None
        or not feeds.snapshot_id
        or not feeds.demand_evidence_id
    ):
        return None
    span = end - start
    horizon = span // timedelta(minutes=1)
    if span % timedelta(minutes=1) or horizon not in SUPPORTED_HORIZONS_MINUTES:
        return None

    records, fetched = feeds.demand_records, feeds.demand_fetched_at_utc
    if feeds.demand_gate_times:
        backlog = departure_backlog(records, fetched, start)
        if backlog.upstream_waiting is None or backlog.runway_queue is None:
            return None
        demand = demand_windows(
            records, fetched, start, horizon, exclude_flight_ids=backlog.flight_ids
        )
        if demand.windows is None:
            return None
        demand = replace(demand, assumptions=demand.assumptions + backlog.assumptions)
        upstream, queue, note = (
            backlog.upstream_waiting,
            backlog.runway_queue,
            OBSERVED_BACKLOG_NOTE,
        )
    elif settings.backlog_upstream is not None and settings.backlog_runway_queue is not None:
        demand = demand_windows(records, fetched, start, horizon)
        if demand.windows is None:
            return None  # missing or stale stored demand: unknown, not zero
        upstream, queue, note = (
            settings.backlog_upstream,
            settings.backlog_runway_queue,
            BACKLOG_NOTE,
        )
    else:
        return None  # no observed backlog and no operator fallback

    snapshot = flow_snapshot_from_demand(feeds.snapshot_id, start, demand, upstream, queue)
    snapshot = replace(snapshot, demand_basis=f"{snapshot.demand_basis}; {note}")
    assumptions = ProjectionAssumptions(
        capacity=CapacityScenario(
            "operator-configured departure capacity",
            settings.capacity_per_hour,
            CapacityBasis.OPERATOR_CONFIGURED,
        ),
        horizon_minutes=horizon,
    )
    response = ResponseParameters(settings.capacity_per_hour, 0, horizon)
    comparison = project(snapshot, assumptions, response)
    if comparison.status is not ProjectionStatus.COMPUTED:
        return None
    return PreparedProjection(
        projection_id=projection_id(feeds.snapshot_id),
        snapshot=snapshot,
        assumptions=assumptions,
        response=response,
        comparison=comparison,
        evidence_id=feeds.demand_evidence_id,
        demand=demand,
        backlog_observed=feeds.demand_gate_times,
    )
