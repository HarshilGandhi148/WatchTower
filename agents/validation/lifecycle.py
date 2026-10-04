"""Advisory identity, versioning, expiry, priority and routing; pure and deterministic.

One advisory per issue/resource/window bucket. A later cycle with the same material
content is suppressed rather than republished; a material change creates the next
version, which supersedes the previous one. Minor position refreshes are not material.
Approved and ignored versions stay suppressed until something material changes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

from agents.validation.calculations import projected_values
from agents.validation.packet import ProjectionPacket, ValidationConfig
from projection.aggregate_flow import ProjectionStatus
from shared.contracts import (
    ActionType,
    AdvisoryPriority,
    AdvisoryStatus,
    AdvisoryType,
    CandidateStatus,
    EvidenceQuality,
    FlowCandidate,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
_SUPPRESSING = frozenset(
    {AdvisoryStatus.PENDING_REVIEW, AdvisoryStatus.APPROVED, AdvisoryStatus.IGNORED}
)


def _digest(payload: object) -> str:
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()


def advisory_id(candidate: FlowCandidate) -> str:
    """Stable ID for the issue, resource and window bucket (one bucket per horizon length)."""
    span = candidate.window_end_utc - candidate.window_start_utc
    bucket = (candidate.window_start_utc - _EPOCH) // span
    key = _digest(
        [
            candidate.envelope.airport_icao,
            candidate.issue_type.value,
            candidate.resource,
            int(span.total_seconds()),
            bucket,
        ]
    )
    return f"adv-{candidate.issue_type.value.lower()}-{key[:16]}"


def material_fingerprint(
    candidate: FlowCandidate,
    action: ActionType | None,
    quality: EvidenceQuality,
    projection: ProjectionPacket | None,
) -> str:
    """What a controller would decide on. Evidence IDs and raw counts are excluded."""
    projected: dict[str, int] = {}
    if projection is not None and projection.comparison.status is ProjectionStatus.COMPUTED:
        projected = projected_values(projection.comparison)
    return _digest(
        {
            "status": candidate.status.value,
            "issue": candidate.issue_type.value,
            "resource": candidate.resource,
            "action": action.value if action else None,
            "parameters": sorted(
                (p.name, p.value, p.unit) for p in candidate.proposed_action_parameters
            ),
            "flights": sorted(candidate.affected_flight_ids),
            "faa_events": sorted(candidate.faa_event_ids),
            "quality": quality.value,
            "projected": projected,
        }
    )


@dataclass(frozen=True, slots=True, kw_only=True)
class PreviousVersion:
    """The latest stored version for an advisory ID, as read back from storage."""

    advisory_id: str
    version: int
    material_fingerprint: str
    status: AdvisoryStatus
    expires_at_utc: datetime


class VersionDecision(StrEnum):
    NEW = "new"
    DUPLICATE = "duplicate"
    SUPERSEDE = "supersede"


@dataclass(frozen=True, slots=True)
class VersionPlan:
    decision: VersionDecision
    version: int


def next_version(
    previous: PreviousVersion | None, fingerprint: str, now_utc: datetime
) -> VersionPlan:
    if previous is None:
        return VersionPlan(VersionDecision.NEW, 1)
    if previous.material_fingerprint == fingerprint and previous.status in _SUPPRESSING:
        still_pending = previous.expires_at_utc > now_utc
        if previous.status is not AdvisoryStatus.PENDING_REVIEW or still_pending:
            return VersionPlan(VersionDecision.DUPLICATE, previous.version)
    return VersionPlan(VersionDecision.SUPERSEDE, previous.version + 1)


def expires_at(candidate: FlowCandidate, now_utc: datetime, config: ValidationConfig) -> datetime:
    """Initial lifetime, never beyond the window the advisory describes."""
    return min(now_utc + config.advisory_lifetime, candidate.window_end_utc)


def priority(
    candidate: FlowCandidate, projection: ProjectionPacket | None, config: ValidationConfig
) -> AdvisoryPriority:
    """Demo rule: notices inform; a projected peak within the first minutes is urgent."""
    if candidate.status is not CandidateStatus.CHANGE_WORTH_REVIEWING:
        return AdvisoryPriority.INFORMATION
    comparison = projection.comparison if projection else None
    if comparison is not None and comparison.baseline is not None:
        if (
            comparison.baseline.metrics.peak_runway_queue_minute
            <= config.time_sensitive_peak_minute
        ):
            return AdvisoryPriority.TIME_SENSITIVE
    return AdvisoryPriority.ATTENTION


def owner_role(issue: AdvisoryType, config: ValidationConfig) -> str:
    return config.owner_roles[issue]
