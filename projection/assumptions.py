"""Explicit capacity/readiness assumptions, response parameters, bounds and model version.

Everything here is declared input. Nothing is inferred, and a missing capacity is
represented as ``None`` so the projection reports unavailable metrics instead of
a default rate. Capacity labelled HYPOTHETICAL must never be shown as an
authoritative airport rate (plan section 8.4).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

MODEL_VERSION = "aggregate-flow-1"
SUPPORTED_HORIZONS_MINUTES = (15, 30)
MAX_TRANSIT_DELAY_MINUTES = 30
MAX_CAPACITY_PER_HOUR = 120


class CapacityBasis(StrEnum):
    """Where a capacity number came from; the model never invents one."""

    HYPOTHETICAL = "hypothetical"
    OPERATOR_CONFIGURED = "operator_configured"
    SOURCE_REPORTED = "source_reported"


def require_count(value: object, name: str, *, minimum: int = 0) -> int:
    """Return ``value`` if it is a real int (not bool) of at least ``minimum``."""
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class CapacityScenario:
    """Effective departure capacity in aircraft per hour for one scenario."""

    name: str
    effective_capacity_per_hour: int
    basis: CapacityBasis

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("Capacity scenario needs a name")
        if not isinstance(self.basis, CapacityBasis):
            raise ValueError("Capacity basis must be a CapacityBasis")
        require_count(self.effective_capacity_per_hour, "effective_capacity_per_hour")
        if self.effective_capacity_per_hour > MAX_CAPACITY_PER_HOUR:
            raise ValueError(f"Capacity above {MAX_CAPACITY_PER_HOUR}/hour is not modeled")


@dataclass(frozen=True, slots=True)
class ResponseBounds:
    """Project limits for a controller-editable release-rate response."""

    min_release_cap_per_hour: int = 6
    max_window_minutes: int = 30

    def __post_init__(self) -> None:
        require_count(self.min_release_cap_per_hour, "min_release_cap_per_hour")
        require_count(self.max_window_minutes, "max_window_minutes", minimum=1)


@dataclass(frozen=True, slots=True)
class ResponseParameters:
    """The only inputs allowed to differ between baseline and proposal.

    ``release_cap_per_hour=None`` means no restriction (the baseline). Otherwise
    the aggregate release rate from upstream waiting toward the runway queue is
    limited in minutes ``[window_start_minute, window_end_minute)`` after the snapshot.
    """

    release_cap_per_hour: int | None = None
    window_start_minute: int = 0
    window_end_minute: int = 0

    def __post_init__(self) -> None:
        require_count(self.window_start_minute, "window_start_minute")
        require_count(self.window_end_minute, "window_end_minute")
        if self.release_cap_per_hour is None:
            return
        require_count(self.release_cap_per_hour, "release_cap_per_hour")
        if self.window_end_minute <= self.window_start_minute:
            raise ValueError("Release window must end after it starts")

    @property
    def is_restriction(self) -> bool:
        return self.release_cap_per_hour is not None


NO_RESPONSE = ResponseParameters()


@dataclass(frozen=True, slots=True)
class ProjectionAssumptions:
    """Model settings shared by baseline and proposal.

    ``transit_delay_weights[d]`` is the integer weight of a released aircraft
    reaching the runway queue ``d`` minutes after release. Weights are apportioned
    with the largest-remainder method so counts stay integral and deterministic.
    Delay 0 only is the simplified instantaneous-transfer schematic and must be
    labelled as such wherever shown.
    """

    capacity: CapacityScenario | None
    horizon_minutes: int = 15
    transit_delay_weights: tuple[int, ...] = (0, 0, 1, 2, 1)
    bounds: ResponseBounds = field(default_factory=ResponseBounds)
    model_version: str = MODEL_VERSION

    def __post_init__(self) -> None:
        if self.horizon_minutes not in SUPPORTED_HORIZONS_MINUTES:
            raise ValueError(f"Horizon must be one of {SUPPORTED_HORIZONS_MINUTES} minutes")
        if not isinstance(self.transit_delay_weights, tuple) or not self.transit_delay_weights:
            raise ValueError("transit_delay_weights must be a non-empty tuple")
        if len(self.transit_delay_weights) > MAX_TRANSIT_DELAY_MINUTES + 1:
            raise ValueError("Transit delay distribution is too long")
        for weight in self.transit_delay_weights:
            require_count(weight, "transit delay weight")
        if sum(self.transit_delay_weights) <= 0:
            raise ValueError("Transit delay weights need a positive total")

    @property
    def simplified_instant_transfer(self) -> bool:
        """True when every released aircraft reaches the runway queue immediately."""
        return all(w == 0 for w in self.transit_delay_weights[1:])


def check_response(response: ResponseParameters, assumptions: ProjectionAssumptions) -> None:
    """Raise ValueError if a response is outside project bounds or the horizon."""
    if not response.is_restriction:
        return
    bounds = assumptions.bounds
    assert response.release_cap_per_hour is not None
    if response.release_cap_per_hour < bounds.min_release_cap_per_hour:
        raise ValueError("Release cap is below the configured model bound")
    if response.window_end_minute > assumptions.horizon_minutes:
        raise ValueError("Release window extends past the projection horizon")
    if response.window_end_minute - response.window_start_minute > bounds.max_window_minutes:
        raise ValueError("Release window is longer than the configured model bound")
