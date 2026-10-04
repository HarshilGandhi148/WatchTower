"""FlightAware AeroAPI adapter (optional, capped). Only the record type exists so far.

TODO (congestion owner): capped requests, pagination, null times/cancellations and
association checks. ``DemandRecord`` is the normalized shape the congestion metrics
consume; the adapter that fills it is not implemented, and nothing here may invent a
record when AeroAPI is disabled or unavailable.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum


class FlightDirection(StrEnum):
    ARRIVAL = "arrival"
    DEPARTURE = "departure"


class TimeKind(StrEnum):
    """What the time field means. Gate times are not runway times (plan 4.5)."""

    SCHEDULED_GATE = "scheduled_gate"
    ESTIMATED_RUNWAY = "estimated_runway"
    ACTUAL_RUNWAY = "actual_runway"


class Association(StrEnum):
    """Whether the flight-to-KDTW link comes from a flight record or was inferred."""

    CONFIRMED_RECORD = "confirmed_record"
    INFERRED = "inferred"


@dataclass(frozen=True, slots=True)
class DemandRecord:
    """One airport-associated flight. A missing time is ``None``, never a default."""

    flight_id: str
    direction: FlightDirection
    time_utc: datetime | None
    time_kind: TimeKind | None
    association: Association
    cancelled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.flight_id, str) or not self.flight_id.strip():
            raise ValueError("flight_id must be a non-empty string")
        if self.time_utc is not None and self.time_utc.utcoffset() != timedelta(0):
            raise ValueError("time_utc must be an aware UTC datetime")
        if (self.time_utc is None) != (self.time_kind is None):
            raise ValueError("time_utc and time_kind must be given together")
