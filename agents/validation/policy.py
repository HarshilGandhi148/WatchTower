"""Bounded action catalog, parameter limits, status/weather rules and untrusted text.

Upstream strings (assumptions, tradeoffs, missing inputs) are data. They are checked
for size and printable characters only, and nothing in them can change the action,
its parameters or the verdict. Quality is never raised above what the candidate claims.
"""

from __future__ import annotations

from collections.abc import Iterable
from types import MappingProxyType

from agents.validation.evidence import horizon_minutes
from agents.validation.packet import (
    SEVERITY_RANK,
    EvidenceKind,
    Finding,
    Severity,
    ValidationConfig,
    ValidationPacket,
)
from projection.assumptions import MAX_CAPACITY_PER_HOUR
from shared.contracts import (
    ActionType,
    AdvisoryType,
    CandidateStatus,
    EvidenceQuality,
    FlowCandidate,
)

# Plan section 7: the only action each advisory type may propose.
ACTION_FOR_ISSUE = MappingProxyType(
    {
        AdvisoryType.WEATHER_FLOW_REVIEW: ActionType.REVIEW_FLOW_WINDOW,
        AdvisoryType.DEPARTURE_RELEASE_REVIEW: ActionType.REVIEW_RELEASE_RATE,
        AdvisoryType.DOWNSTREAM_RESTRICTION: ActionType.COORDINATE_DOWNSTREAM_RESTRICTION,
        AdvisoryType.TRAFFIC_PRESSURE_NOTICE: ActionType.INFORMATION_NOTICE,
    }
)

WINDOW_UNIT = "minutes after snapshot"
PARAMETER_UNITS = MappingProxyType(
    {
        "release_cap": "aircraft/hour",
        "window_start_minute": WINDOW_UNIT,
        "window_end_minute": WINDOW_UNIT,
    }
)
_WINDOW = frozenset({"window_start_minute", "window_end_minute"})
REQUIRED_PARAMETERS = MappingProxyType(
    {
        ActionType.REVIEW_RELEASE_RATE: _WINDOW | {"release_cap"},
        ActionType.REVIEW_FLOW_WINDOW: _WINDOW,
        ActionType.COORDINATE_DOWNSTREAM_RESTRICTION: frozenset[str](),
        ActionType.INFORMATION_NOTICE: frozenset[str](),
    }
)
# Actions whose numbers must come from a verified projection.
PROJECTION_BACKED = frozenset({ActionType.REVIEW_RELEASE_RATE})


def proposed_action(candidate: FlowCandidate) -> ActionType | None:
    """The action an advisory would carry. Information-only always becomes a notice."""
    if candidate.status is CandidateStatus.INFORMATION_ONLY:
        return ActionType.INFORMATION_NOTICE
    if candidate.status is CandidateStatus.CHANGE_WORTH_REVIEWING:
        return candidate.proposed_action_type
    return None


def check_status(candidate: FlowCandidate) -> tuple[Finding, ...]:
    if candidate.status is CandidateStatus.INSUFFICIENT_DATA:
        missing = "; ".join(candidate.required_missing_inputs)
        return (
            Finding("candidate_insufficient_data", Severity.MISSING, f"missing inputs: {missing}"),
        )
    if candidate.status is CandidateStatus.NO_CHANGE:
        return (Finding("no_change", Severity.NO_ADVISORY, "no change worth reviewing"),)
    return ()


def check_catalog(candidate: FlowCandidate) -> tuple[Finding, ...]:
    if candidate.status is not CandidateStatus.CHANGE_WORTH_REVIEWING:
        return ()
    if candidate.proposed_action_type is not ACTION_FOR_ISSUE[candidate.issue_type]:
        return (
            Finding(
                "action_not_in_catalog",
                Severity.INTEGRITY,
                f"{candidate.issue_type.value} cannot propose this action",
            ),
        )
    return ()


def check_parameters(packet: ValidationPacket, config: ValidationConfig) -> tuple[Finding, ...]:
    candidate = packet.candidate
    assert candidate is not None
    action = proposed_action(candidate)
    if action is None:
        return ()
    return tuple(_parameter_findings(packet, config, candidate, action))


def _parameter_findings(
    packet: ValidationPacket,
    config: ValidationConfig,
    candidate: FlowCandidate,
    action: ActionType,
) -> Iterable[Finding]:
    required = REQUIRED_PARAMETERS[action]
    values: dict[str, int] = {}
    for parameter in candidate.proposed_action_parameters:
        if parameter.name not in required:
            yield Finding(
                "unknown_parameter", Severity.INTEGRITY, f"{parameter.name} is not allowed here"
            )
        elif parameter.unit != PARAMETER_UNITS[parameter.name]:
            yield Finding("parameter_unit", Severity.INTEGRITY, f"{parameter.name} unit invalid")
        elif type(parameter.value) is not int:
            yield Finding(
                "parameter_not_integer", Severity.INTEGRITY, f"{parameter.name} must be whole"
            )
        else:
            values[parameter.name] = parameter.value
    for name in sorted(required - {p.name for p in candidate.proposed_action_parameters}):
        yield Finding("missing_parameter", Severity.INTEGRITY, f"{name} is required")

    projection = packet.projection
    bounds = projection.assumptions.bounds if projection else config.response_bounds
    if "release_cap" in values:
        cap = values["release_cap"]
        ceiling = MAX_CAPACITY_PER_HOUR
        capacity = projection.assumptions.capacity if projection else None
        if capacity is not None:
            ceiling = min(ceiling, capacity.effective_capacity_per_hour)
        if not bounds.min_release_cap_per_hour <= cap <= ceiling:
            yield Finding(
                "release_cap_out_of_bounds",
                Severity.INTEGRITY,
                f"release cap must be {bounds.min_release_cap_per_hour}-{ceiling} aircraft/hour",
            )
    if _WINDOW <= values.keys():
        start, end = values["window_start_minute"], values["window_end_minute"]
        horizon = horizon_minutes(candidate)
        if not 0 <= start < end <= horizon or end - start > bounds.max_window_minutes:
            yield Finding(
                "window_out_of_bounds",
                Severity.INTEGRITY,
                f"window must lie within the {horizon}-minute horizon and at most "
                f"{bounds.max_window_minutes} minutes",
            )


def _cited_kinds(packet: ValidationPacket) -> set[EvidenceKind]:
    candidate = packet.candidate
    assert candidate is not None
    return {
        packet.evidence[i].kind for i in candidate.envelope.evidence_ids if i in packet.evidence
    }


def check_requirements(packet: ValidationPacket) -> tuple[Finding, ...]:
    """Required evidence for each advisory type (plan section 7 catalog)."""
    candidate = packet.candidate
    assert candidate is not None
    if candidate.status is not CandidateStatus.CHANGE_WORTH_REVIEWING:
        return ()
    kinds = _cited_kinds(packet)
    findings: list[Finding] = []
    action = candidate.proposed_action_type
    if action in PROJECTION_BACKED and candidate.projection_id is None:
        findings.append(
            Finding("projection_required", Severity.MISSING, "a release-rate review needs one")
        )
    if candidate.issue_type is AdvisoryType.DEPARTURE_RELEASE_REVIEW and (
        EvidenceKind.DEMAND not in kinds
    ):
        findings.append(
            Finding("demand_required", Severity.MISSING, "needs airport-associated demand")
        )
    if candidate.issue_type is AdvisoryType.WEATHER_FLOW_REVIEW and not (
        kinds & {EvidenceKind.TRAFFIC, EvidenceKind.DEMAND}
    ):
        findings.append(
            Finding("traffic_required", Severity.MISSING, "needs a supported traffic assessment")
        )
    if candidate.issue_type is AdvisoryType.DOWNSTREAM_RESTRICTION and not (
        EvidenceKind.FAA_EVENT in kinds and EvidenceKind.DEMAND in kinds
    ):
        findings.append(
            Finding(
                "restriction_unsupported",
                Severity.MISSING,
                "needs a published FAA event and flight records that associate flights",
            )
        )
    return tuple(findings)


def check_weather(packet: ValidationPacket) -> tuple[Finding, ...]:
    """Weather must agree with the issue; clear or unknown weather never blocks traffic."""
    candidate = packet.candidate
    assert candidate is not None
    weather = packet.weather
    weather_issue = candidate.issue_type is AdvisoryType.WEATHER_FLOW_REVIEW
    actionable = candidate.status is CandidateStatus.CHANGE_WORTH_REVIEWING
    if weather is None:
        if weather_issue and actionable:
            return (Finding("weather_assessment_missing", Severity.MISSING, "weather not loaded"),)
        return (
            Finding(
                "weather_assessment_unavailable",
                Severity.LIMITATION,
                "weather assessment unavailable for this case",
            ),
        )
    if weather_issue and weather.status != "impact":
        return (
            Finding(
                "weather_contradiction",
                Severity.RECOVERABLE,
                f"weather-flow review but the weather assessment is {weather.status}",
            ),
        )
    if weather.status == "impact" and actionable and not weather_issue:
        return (
            Finding(
                "weather_impact_not_reflected",
                Severity.RECOVERABLE,
                "weather reports an impact that the actionable candidate does not consider",
            ),
        )
    if weather.status == "insufficient_data":
        return (
            Finding(
                "weather_insufficient",
                Severity.LIMITATION,
                "weather assessment had insufficient data",
            ),
        )
    return ()


def check_untrusted_text(candidate: FlowCandidate, config: ValidationConfig) -> tuple[Finding, ...]:
    limit = config.max_untrusted_text
    texts = (
        candidate.resource,
        *candidate.assumptions,
        *candidate.tradeoffs,
        *candidate.required_missing_inputs,
    )
    if any(len(text) > limit or any(ord(c) < 32 for c in text) for text in texts):
        return (
            Finding(
                "untrusted_text_invalid",
                Severity.INTEGRITY,
                f"candidate text must be printable and at most {limit} characters",
            ),
        )
    return ()


def assess_quality(candidate: FlowCandidate, findings: Iterable[Finding]) -> EvidenceQuality:
    """Never better than the candidate claims; any limitation makes it limited."""
    quality = candidate.quality
    limited = any(f.severity is Severity.LIMITATION for f in findings)
    if limited and quality is EvidenceQuality.ADEQUATE:
        return EvidenceQuality.LIMITED
    return quality


def worst(findings: Iterable[Finding]) -> Severity | None:
    ranked = sorted(findings, key=lambda f: SEVERITY_RANK[f.severity])
    return ranked[-1].severity if ranked else None
