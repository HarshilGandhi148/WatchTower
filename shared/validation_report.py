"""Versioned, informational validator report; never a controller decision."""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

CheckStatus = Literal["pass", "fail", "not_applicable"]
ReportOutcome = Literal["supported", "insufficient_data", "rejected"]


@dataclass(frozen=True, slots=True)
class AircraftRecommendation:
    aircraft_id: str
    label: str
    recommendation: str
    prediction: str
    evidence_id: str
    observed_at_utc: datetime
    forecast_at_utc: datetime | None
    projected_latitude_deg: float | None
    projected_longitude_deg: float | None


@dataclass(frozen=True, slots=True)
class ValidationCheck:
    name: str
    status: CheckStatus
    reason: str
    evidence_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ValidationReport:
    report_id: str
    candidate_id: str
    snapshot_id: str
    case_id: str
    assessment_round: int
    created_at_utc: datetime
    expires_at_utc: datetime
    outcome: ReportOutcome
    checks: tuple[ValidationCheck, ...]
    suggestion: str
    limitations: tuple[str, ...]
    schema_version: int = 1
    recommendation_kind: Literal["demo", "monitoring"] = "monitoring"
    aircraft_recommendations: tuple[AircraftRecommendation, ...] = ()
