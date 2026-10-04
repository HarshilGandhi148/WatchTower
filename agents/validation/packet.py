"""Already-loaded inputs, findings and configuration for the pure validation checks.

These DTOs are private to the Validation workstream. A later loader fills a
ValidationPacket from stored records; the checks never fetch anything themselves.
Thresholds here are demo configuration from plan section 10, not ATC standards.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from types import MappingProxyType

from projection.aggregate_flow import FlowSnapshot, ProjectionComparison
from projection.assumptions import ProjectionAssumptions, ResponseBounds
from shared.contracts import AdvisoryType, FlowCandidate, MessageEnvelope
from shared.messages import WEATHER_STATUSES
from shared.orchestration import CandidateReady


class Severity(StrEnum):
    """How a finding affects the verdict, from least to most severe."""

    LIMITATION = "limitation"  # publishable; lowers quality and is shown to controllers
    RECOVERABLE = "recoverable"  # a specialist reassessment could resolve it
    NO_ADVISORY = "no_advisory"  # valid, but nothing to present (no change, duplicate)
    MISSING = "missing"  # required input is absent or stale: insufficient data
    INTEGRITY = "integrity"  # inconsistent, unsupported or out-of-bounds: reject


SEVERITY_RANK = {severity: rank for rank, severity in enumerate(Severity)}


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    severity: Severity
    detail: str


class EvidenceKind(StrEnum):
    TRAFFIC = "traffic"
    DEMAND = "demand"
    FAA_EVENT = "faa_event"
    WEATHER = "weather"
    PROJECTION = "projection"


def _require_utc(value: object, name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError(f"{name} must be an aware UTC datetime")


@dataclass(frozen=True, slots=True, kw_only=True)
class EvidenceRecord:
    """Provenance of one stored record. ``source_time_utc`` is the provider's own time."""

    evidence_id: str
    kind: EvidenceKind
    airport_icao: str
    snapshot_id: str
    data_mode: str
    fetched_at_utc: datetime
    source_time_utc: datetime | None = None
    valid_from_utc: datetime | None = None
    valid_until_utc: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, EvidenceKind):
            raise ValueError("kind must be an EvidenceKind")
        for name in ("evidence_id", "airport_icao", "snapshot_id", "data_mode"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string")
        _require_utc(self.fetched_at_utc, "fetched_at_utc")
        for name in ("source_time_utc", "valid_from_utc", "valid_until_utc"):
            if getattr(self, name) is not None:
                _require_utc(getattr(self, name), name)


@dataclass(frozen=True, slots=True, kw_only=True)
class WeatherFinding:
    """Provisional reference to a stored weather assessment until its contract lands."""

    assessment_id: str
    case_id: str
    snapshot_id: str
    status: str

    def __post_init__(self) -> None:
        if self.status not in WEATHER_STATUSES:
            raise ValueError("Unknown weather assessment status")


@dataclass(frozen=True, slots=True, kw_only=True)
class ProjectionPacket:
    """The stored inputs and result behind a candidate's ``projection_id``."""

    projection_id: str
    snapshot: FlowSnapshot
    assumptions: ProjectionAssumptions
    comparison: ProjectionComparison


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationPacket:
    """Everything one validation run reads. ``None`` means it could not be loaded."""

    opening: MessageEnvelope
    ready: CandidateReady
    candidate: FlowCandidate | None
    evidence: Mapping[str, EvidenceRecord]
    weather: WeatherFinding | None
    projection: ProjectionPacket | None
    now_utc: datetime

    def __post_init__(self) -> None:
        _require_utc(self.now_utc, "now_utc")

    @property
    def snapshot_at_utc(self) -> datetime:
        """The case snapshot cutoff: when the opening specialist fixed its evidence."""
        return self.opening.created_at_utc


DEMO_OWNER_ROLES: Mapping[AdvisoryType, str] = MappingProxyType(
    {
        AdvisoryType.WEATHER_FLOW_REVIEW: "Flow coordinator",
        AdvisoryType.DEPARTURE_RELEASE_REVIEW: "Departure flow coordinator",
        AdvisoryType.DOWNSTREAM_RESTRICTION: "Traffic management coordinator",
        AdvisoryType.TRAFFIC_PRESSURE_NOTICE: "Flow coordinator",
    }
)


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidationConfig:
    """Demo settings (plan section 10). Owner roles are illustrative, not DTW staffing."""

    position_stale_after: timedelta = timedelta(seconds=60)
    position_excluded_after: timedelta = timedelta(seconds=120)
    weather_limited_after: timedelta = timedelta(minutes=90)
    faa_flagged_after: timedelta = timedelta(minutes=5)
    demand_excluded_after: timedelta = timedelta(minutes=5)
    window_start_tolerance: timedelta = timedelta(seconds=60)
    advisory_lifetime: timedelta = timedelta(seconds=120)
    time_sensitive_peak_minute: int = 5
    max_untrusted_text: int = 500
    response_bounds: ResponseBounds = field(default_factory=ResponseBounds)
    owner_roles: Mapping[AdvisoryType, str] = DEMO_OWNER_ROLES

    def __post_init__(self) -> None:
        if set(self.owner_roles) != set(AdvisoryType):
            raise ValueError("Every advisory type needs an owner role")
        if self.position_excluded_after < self.position_stale_after:
            raise ValueError("Positions cannot be excluded before they are stale")
        if self.advisory_lifetime <= timedelta(0):
            raise ValueError("Advisory lifetime must be positive")

    def describe(self) -> str:
        return (
            "validator thresholds are demo configuration: positions stale after "
            f"{self.position_stale_after.total_seconds():.0f} s and excluded after "
            f"{self.position_excluded_after.total_seconds():.0f} s, flight records after "
            f"{self.demand_excluded_after.total_seconds() / 60:.0f} min, advisory lifetime "
            f"{self.advisory_lifetime.total_seconds():.0f} s"
        )
