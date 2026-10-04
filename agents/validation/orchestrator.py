"""Bounded case routing owned by the existing Validation / Coordinating Agent.

This service creates no identities and never writes live observations or decisions.
State is in memory until the backend owner supplies durable case persistence.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from types import MappingProxyType
from uuid import uuid4

from shared.contracts import AgentName, MessageEnvelope
from shared.orchestration import AssessmentRequest, CandidateReady, CasePhase, ValidationVerdict

ACTIVE_PHASES = frozenset({CasePhase.ASSESSING, CasePhase.VALIDATING})


class OrchestrationError(ValueError):
    """Invalid peer, evidence references, correlation, or lifecycle transition."""


@dataclass(frozen=True, slots=True)
class Dispatch:
    recipient: str
    request: AssessmentRequest


@dataclass(frozen=True, slots=True)
class CaseRecord:
    opening: MessageEnvelope
    request: AssessmentRequest
    deadline: datetime
    phase: CasePhase = CasePhase.ASSESSING
    candidate: CandidateReady | None = None
    received_candidates: tuple[CandidateReady, ...] = ()
    reason: str | None = None
    notified: bool = False


class Orchestrator:
    """Route specialists and retain one active assessment for each case ID.

    Call synchronously from handlers before awaiting network/domain work. Case IDs
    must be deduplicated by issue/resource/window upstream, as specified in the plan.
    """

    def __init__(
        self,
        addresses: Mapping[AgentName, str],
        *,
        timeout: timedelta = timedelta(seconds=60),
        max_cases: int = 256,
    ) -> None:
        if set(addresses) != set(AgentName):
            raise OrchestrationError("Exactly three configured agent roles are required")
        if any(not value.strip() for value in addresses.values()):
            raise OrchestrationError("Every role needs a configured address")
        if len(set(addresses.values())) != 3:
            raise OrchestrationError("Agent identities must be distinct")
        if timeout <= timedelta(0) or max_cases < 1:
            raise OrchestrationError("Timeout and case capacity must be positive")
        self.addresses = MappingProxyType(dict(addresses))
        self.timeout = timeout
        self.max_cases = max_cases
        self._cases: dict[str, CaseRecord] = {}

    def open_case(
        self, sender: str, envelope: MessageEnvelope, *, now: datetime | None = None
    ) -> Dispatch | None:
        moment = self._moment(now)
        self._authenticate(sender, envelope, {AgentName.WEATHER, AgentName.CONGESTION}, moment)
        existing = self._cases.get(envelope.case_id)
        if existing is not None:
            self._same_snapshot(existing.opening, envelope)
            return None
        if len(self._cases) >= self.max_cases:
            raise OrchestrationError("Case capacity reached; expire and persist existing cases")
        deadline = min(moment + self.timeout, envelope.valid_until_utc)
        request = self._request(envelope, 1, moment, deadline, envelope.message_id)
        self._cases[envelope.case_id] = CaseRecord(envelope, request, deadline)
        return Dispatch(self.addresses[AgentName.CONGESTION], request)

    def receive_candidate(
        self, sender: str, candidate: CandidateReady, *, now: datetime | None = None
    ) -> CandidateReady | None:
        moment = self._moment(now)
        self._authenticate(sender, candidate.envelope, {AgentName.CONGESTION}, moment)
        case = self.get_case(candidate.envelope.case_id)
        self._same_snapshot(case.opening, candidate.envelope)
        for previous in case.received_candidates:
            if previous.envelope.message_id == candidate.envelope.message_id:
                if previous != candidate:
                    raise OrchestrationError("A retried message changed its payload")
                return None
        self._require_active(case, moment)
        if case.phase != CasePhase.ASSESSING:
            raise OrchestrationError("Validation is already in progress")
        if candidate.assessment_round != case.request.assessment_round:
            raise OrchestrationError("Candidate belongs to an outdated assessment round")
        if candidate.envelope.reply_to != case.request.envelope.message_id:
            raise OrchestrationError("Candidate does not reply to the current assessment")
        if candidate.envelope.correlation_id != case.request.envelope.correlation_id:
            raise OrchestrationError("Candidate correlation does not match the case")
        if candidate.envelope.valid_until_utc > case.deadline:
            raise OrchestrationError("Candidate cannot extend case validity")
        self._cases[case.opening.case_id] = replace(
            case,
            phase=CasePhase.VALIDATING,
            candidate=candidate,
            received_candidates=case.received_candidates + (candidate,),
        )
        return candidate

    def finish_validation(
        self,
        candidate: CandidateReady,
        verdict: ValidationVerdict,
        *,
        now: datetime | None = None,
    ) -> Dispatch | None:
        moment = self._moment(now)
        case = self.get_case(candidate.envelope.case_id)
        self._require_active(case, moment)
        if candidate.envelope.valid_until_utc <= moment:
            raise OrchestrationError("Candidate expired before validation finished")
        if case.phase != CasePhase.VALIDATING or case.candidate != candidate:
            raise OrchestrationError("Validation result no longer matches the current candidate")
        if not isinstance(verdict, ValidationVerdict):
            raise OrchestrationError("A typed domain validation verdict is required")
        if verdict == ValidationVerdict.REASSESS and case.request.assessment_round == 1:
            request = self._request(
                case.opening, 2, moment, case.deadline, candidate.envelope.message_id
            )
            self._cases[case.opening.case_id] = replace(
                case, phase=CasePhase.ASSESSING, request=request, candidate=None
            )
            return Dispatch(self.addresses[AgentName.CONGESTION], request)
        phase = {
            ValidationVerdict.PUBLISH: CasePhase.READY_TO_PUBLISH,
            ValidationVerdict.REJECT: CasePhase.REJECTED,
            ValidationVerdict.INSUFFICIENT_DATA: CasePhase.INSUFFICIENT_DATA,
            ValidationVerdict.REASSESS: CasePhase.INSUFFICIENT_DATA,
        }[verdict]
        reason = "reassessment_limit" if verdict == ValidationVerdict.REASSESS else verdict.value
        self._cases[case.opening.case_id] = replace(case, phase=phase, reason=reason)
        return None

    def fail_case(self, case_id: str, reason: str) -> None:
        case = self.get_case(case_id)
        if case.phase in ACTIVE_PHASES:
            self._cases[case_id] = replace(case, phase=CasePhase.INSUFFICIENT_DATA, reason=reason)

    def expire(self, *, now: datetime | None = None) -> tuple[CaseRecord, ...]:
        moment = self._moment(now)
        expired: list[CaseRecord] = []
        for case_id, case in tuple(self._cases.items()):
            cutoff = (
                min(case.deadline, case.candidate.envelope.valid_until_utc)
                if case.candidate is not None
                else case.deadline
            )
            waiting_to_publish = case.phase == CasePhase.READY_TO_PUBLISH and not case.notified
            if (case.phase in ACTIVE_PHASES or waiting_to_publish) and moment >= cutoff:
                closed = replace(case, phase=CasePhase.INSUFFICIENT_DATA, reason="timeout")
                self._cases[case_id] = closed
                expired.append(closed)
        return tuple(expired)

    def pending_notifications(self) -> tuple[CaseRecord, ...]:
        return tuple(
            case
            for case in self._cases.values()
            if case.phase not in ACTIVE_PHASES and not case.notified
        )

    def mark_notified(self, case_id: str) -> None:
        case = self.get_case(case_id)
        if case.phase in ACTIVE_PHASES:
            raise OrchestrationError("Active cases cannot be marked persisted")
        self._cases[case_id] = replace(case, notified=True)

    def prune(self, *, now: datetime | None = None) -> None:
        moment = self._moment(now)
        for case_id, case in tuple(self._cases.items()):
            if case.notified and moment >= case.opening.valid_until_utc:
                del self._cases[case_id]

    def get_case(self, case_id: str) -> CaseRecord:
        try:
            return self._cases[case_id]
        except KeyError as error:
            raise OrchestrationError("Unknown case") from error

    def _authenticate(
        self,
        sender: str,
        envelope: MessageEnvelope,
        roles: set[AgentName],
        now: datetime,
    ) -> None:
        if envelope.sender_agent not in roles or self.addresses[envelope.sender_agent] != sender:
            raise OrchestrationError("Sender does not match the configured specialist identity")
        if envelope.created_at_utc > now or envelope.valid_until_utc <= now:
            raise OrchestrationError("Message is not currently valid")

    @staticmethod
    def _same_snapshot(original: MessageEnvelope, incoming: MessageEnvelope) -> None:
        if (
            original.airport_icao != incoming.airport_icao
            or original.snapshot_id != incoming.snapshot_id
            or set(original.evidence_ids) != set(incoming.evidence_ids)
        ):
            raise OrchestrationError("Case evidence must remain on its original snapshot")

    @staticmethod
    def _require_active(case: CaseRecord, now: datetime) -> None:
        if case.phase not in ACTIVE_PHASES or now >= case.deadline:
            raise OrchestrationError("Case is closed or timed out")

    @staticmethod
    def _moment(value: datetime | None) -> datetime:
        moment = value if value is not None else datetime.now(UTC)
        if moment.utcoffset() != timedelta(0):
            raise OrchestrationError("Coordinator time must be aware UTC")
        return moment

    @staticmethod
    def _request(
        opening: MessageEnvelope,
        assessment_round: int,
        now: datetime,
        deadline: datetime,
        reply_to: str,
    ) -> AssessmentRequest:
        envelope = MessageEnvelope(
            schema_version=opening.schema_version,
            message_id=str(uuid4()),
            case_id=opening.case_id,
            sender_agent=AgentName.VALIDATION,
            created_at_utc=now,
            snapshot_id=opening.snapshot_id,
            evidence_ids=opening.evidence_ids,
            airport_icao=opening.airport_icao,
            valid_until_utc=deadline,
            correlation_id=opening.correlation_id or opening.message_id,
            reply_to=reply_to,
        )
        return AssessmentRequest(envelope, assessment_round)
