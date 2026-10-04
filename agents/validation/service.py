"""Pure validation entry point: one loaded packet in, one verdict and optional advisory out.

The future ``validate`` callback (agents/validation/protocol.py) loads a
ValidationPacket from storage, calls ``validate_packet`` and keeps the advisory for
the idempotent ``persist`` callback. Nothing here reads storage, clocks or networks.

Verdict from the most severe finding: integrity or no-advisory -> reject, missing ->
insufficient data, recoverable -> reassess (the coordinator allows one), otherwise
publish a pending-review advisory. Limitations are published, lowering quality.
"""

from __future__ import annotations

from dataclasses import dataclass

from agents.validation import lifecycle, policy, wording
from agents.validation.calculations import check_projection
from agents.validation.evidence import (
    check_evidence,
    check_references,
    check_window,
    cited_evidence_ids,
)
from agents.validation.lifecycle import PreviousVersion, VersionDecision
from agents.validation.packet import Finding, Severity, ValidationConfig, ValidationPacket
from shared.contracts import FlowCandidate, ValidatedAdvisory
from shared.orchestration import ValidationVerdict

_VERDICT = {
    Severity.INTEGRITY: ValidationVerdict.REJECT,
    Severity.MISSING: ValidationVerdict.INSUFFICIENT_DATA,
    Severity.NO_ADVISORY: ValidationVerdict.REJECT,
    Severity.RECOVERABLE: ValidationVerdict.REASSESS,
}


@dataclass(frozen=True, slots=True)
class ValidationOutcome:
    verdict: ValidationVerdict
    findings: tuple[Finding, ...]
    advisory: ValidatedAdvisory | None = None


def validate_packet(
    packet: ValidationPacket,
    config: ValidationConfig | None = None,
    previous: PreviousVersion | None = None,
) -> ValidationOutcome:
    settings = config or ValidationConfig()
    findings = list(check_references(packet))
    candidate = packet.candidate
    if candidate is None:
        return _outcome(findings)
    findings += check_window(packet, settings)
    findings += check_evidence(packet, settings)
    findings += check_projection(packet)
    findings += policy.check_status(candidate)
    findings += policy.check_catalog(candidate)
    findings += policy.check_parameters(packet, settings)
    findings += policy.check_requirements(packet)
    findings += policy.check_weather(packet)
    findings += policy.check_untrusted_text(candidate, settings)

    outcome = _outcome(findings)
    if outcome.verdict is not ValidationVerdict.PUBLISH:
        return outcome
    return _publish(packet, candidate, settings, tuple(findings), previous)


def _outcome(findings: list[Finding]) -> ValidationOutcome:
    severity = policy.worst(findings)
    verdict = ValidationVerdict.PUBLISH
    if severity is not None:
        verdict = _VERDICT.get(severity, ValidationVerdict.PUBLISH)
    return ValidationOutcome(verdict, tuple(findings))


def _publish(
    packet: ValidationPacket,
    candidate: FlowCandidate,
    config: ValidationConfig,
    findings: tuple[Finding, ...],
    previous: PreviousVersion | None,
) -> ValidationOutcome:
    action = policy.proposed_action(candidate)
    quality = policy.assess_quality(candidate, findings)
    advisory_id = lifecycle.advisory_id(candidate)
    fingerprint = lifecycle.material_fingerprint(candidate, action, quality, packet.projection)
    if previous is not None and previous.advisory_id != advisory_id:
        previous = None  # a different issue/window bucket is a new advisory
    plan = lifecycle.next_version(previous, fingerprint, packet.now_utc)
    if plan.decision is VersionDecision.DUPLICATE:
        duplicate = Finding(
            "duplicate_advisory",
            Severity.NO_ADVISORY,
            f"unchanged from {advisory_id} version {plan.version}",
        )
        return ValidationOutcome(ValidationVerdict.REJECT, (*findings, duplicate))

    text = wording.compose(
        candidate, packet.projection, packet.weather.status if packet.weather else None
    )
    limitations = [f.detail for f in findings if f.severity is Severity.LIMITATION]
    limitations += candidate.required_missing_inputs
    if candidate.projection_id is not None:
        limitations.append(wording.PROJECTION_CAPTION)
    try:
        advisory = ValidatedAdvisory(
            advisory_id=advisory_id,
            version=plan.version,
            case_id=candidate.envelope.case_id,
            snapshot_id=candidate.envelope.snapshot_id,
            advisory_type=candidate.issue_type,
            title=text.title,
            suggested_response=text.suggested_response,
            reason=text.reason,
            priority=lifecycle.priority(candidate, packet.projection, config),
            owner_role=lifecycle.owner_role(candidate.issue_type, config),
            affected_resource=candidate.resource,
            window_start_utc=candidate.window_start_utc,
            window_end_utc=candidate.window_end_utc,
            evidence_ids=cited_evidence_ids(candidate),
            quality=quality,
            material_fingerprint=fingerprint,
            created_at_utc=packet.now_utc,
            expires_at_utc=lifecycle.expires_at(candidate, packet.now_utc, config),
            affected_flight_ids=candidate.affected_flight_ids,
            projection_id=candidate.projection_id,
            action_type=action,
            action_parameters=candidate.proposed_action_parameters,
            tradeoffs=text.tradeoffs,
            assumptions=(*candidate.assumptions, config.describe()),
            limitations=tuple(dict.fromkeys(limitations)),
        )
    except ValueError as error:
        rejected = Finding("advisory_contract", Severity.INTEGRITY, str(error))
        return ValidationOutcome(ValidationVerdict.REJECT, (*findings, rejected))
    return ValidationOutcome(ValidationVerdict.PUBLISH, findings, advisory)
