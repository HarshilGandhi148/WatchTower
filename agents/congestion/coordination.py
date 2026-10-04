"""Fixed-snapshot case coordination: bounded exchanges, timeouts, idempotent retries.

A pure state machine; no I/O, clock, uAgents or other-agent imports. The runtime passes
``now`` and acts on what is returned. Handlers contain no ``await``, so under asyncio
each call is atomic and a case never has two overlapping assessment runs.

Rules (plan 6.5):
- A case is bound to one snapshot. Retries of ``open_case`` return the same request, so
  duplicate or retried messages yield one case; a different snapshot for the same case
  id is a conflict.
- One weather exchange per case, plus at most one more if validation asks for a single
  correction: at most two specialist exchanges (``AssessmentRequest.MAX_ATTEMPT``).
- A weather timeout or an insufficient reply proceeds with an UNKNOWN weather signal
  (limited quality downstream); clear weather never blocks. A later reply to a timed-out
  request is rejected, never applied.
- A second correction request closes the case with no further actionable output.
- A reply is accepted only from the weather role, for this case and snapshot, answering
  the outstanding request, before its own expiry. The declared role is structural, not
  authentication; the runtime verifies the actual sender address.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from agents.congestion.candidates import WeatherSignal
from shared.contracts import (
    AgentName,
    AssessmentRequest,
    FlowCandidate,
    MessageEnvelope,
)

DEFAULT_WEATHER_TIMEOUT = timedelta(seconds=15)
DEFAULT_CASE_TTL = timedelta(seconds=120)
MAX_CORRECTIONS = 1


class CoordinationError(Exception):
    """Base class for caller errors (as opposed to rejected messages)."""


class UnknownCaseError(CoordinationError):
    pass


class CaseConflictError(CoordinationError):
    """The case id is already bound to a different snapshot."""


class CaseClosedError(CoordinationError):
    pass


class NotReadyError(CoordinationError):
    """The weather exchange is still outstanding."""


class CaseState(StrEnum):
    AWAITING_WEATHER = "awaiting_weather"
    READY = "ready"
    SUBMITTED = "submitted"
    CLOSED = "closed"


class CloseReason(StrEnum):
    EXPIRED = "expired"
    PERSISTENT_DISAGREEMENT = "persistent_disagreement"


class Outcome(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class ReplyResult:
    outcome: Outcome
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CorrectionResult:
    """``request`` is the second weather request to send when a correction is accepted."""

    outcome: Outcome
    request: AssessmentRequest | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class CaseView:
    """Immutable summary of a case."""

    case_id: str
    snapshot_id: str
    state: CaseState
    exchanges: int
    corrections: int
    weather_signal: WeatherSignal
    weather_assessment_id: str | None
    weather_timed_out: bool
    candidates_sent: int
    close_reason: CloseReason | None


@dataclass(slots=True)
class _Case:
    case_id: str
    snapshot_id: str
    evidence_ids: tuple[str, ...]
    window_start: datetime
    window_end: datetime
    valid_until: datetime
    state: CaseState = CaseState.AWAITING_WEATHER
    requests: list[AssessmentRequest] = field(default_factory=list)
    deadline: datetime | None = None
    exchanges: int = 0
    corrections: int = 0
    signal: WeatherSignal = WeatherSignal.UNKNOWN
    assessment_id: str | None = None
    timed_out: bool = False
    answered_message_ids: set[str] = field(default_factory=set)
    candidates: list[FlowCandidate] = field(default_factory=list)
    correction_results: dict[str, CorrectionResult] = field(default_factory=dict)
    close_reason: CloseReason | None = None
    reply_to: str | None = None  # the correction request the latest revision answers


class CaseCoordinator:
    def __init__(
        self,
        *,
        weather_timeout: timedelta = DEFAULT_WEATHER_TIMEOUT,
        case_ttl: timedelta = DEFAULT_CASE_TTL,
    ) -> None:
        if weather_timeout <= timedelta(0) or case_ttl <= weather_timeout:
            raise ValueError("need 0 < weather_timeout < case_ttl")
        self._weather_timeout = weather_timeout
        self._case_ttl = case_ttl
        self._cases: dict[str, _Case] = {}

    # -- opening ---------------------------------------------------------------------

    def open_case(
        self,
        case_id: str,
        snapshot_id: str,
        evidence_ids: tuple[str, ...],
        now: datetime,
        window_start: datetime,
        window_end: datetime,
    ) -> AssessmentRequest:
        """Open a case bound to a fixed snapshot, or return the existing case's request."""
        _require_utc(now)
        existing = self._cases.get(case_id)
        if existing is not None:
            if existing.snapshot_id != snapshot_id:
                raise CaseConflictError(f"case {case_id} is bound to another snapshot")
            return existing.requests[-1]
        case = _Case(
            case_id, snapshot_id, evidence_ids, window_start, window_end, now + self._case_ttl
        )
        request = self._new_request(case, now)  # validates ids, window and evidence
        self._cases[case_id] = case
        return request

    def _new_request(self, case: _Case, now: datetime) -> AssessmentRequest:
        attempt = case.exchanges + 1
        envelope = MessageEnvelope(
            schema_version=MessageEnvelope.SUPPORTED_SCHEMA_VERSION,
            message_id=f"{case.case_id}:weather-request:{attempt}",
            case_id=case.case_id,
            sender_agent=AgentName.CONGESTION,
            created_at_utc=now,
            snapshot_id=case.snapshot_id,
            evidence_ids=case.evidence_ids,
            airport_icao="KDTW",
            valid_until_utc=case.valid_until,
        )
        request = AssessmentRequest(envelope, case.window_start, case.window_end, attempt)
        case.requests.append(request)
        case.exchanges = attempt
        case.state = CaseState.AWAITING_WEATHER
        case.deadline = now + self._weather_timeout
        return request

    # -- weather replies -------------------------------------------------------------

    def on_weather_reply(
        self,
        envelope: MessageEnvelope,
        signal: WeatherSignal,
        assessment_id: str | None,
        now: datetime,
    ) -> ReplyResult:
        """Apply a weather reply if, and only if, it answers the outstanding request."""
        _require_utc(now)
        case = self._cases.get(envelope.case_id)
        if case is None:
            return ReplyResult(Outcome.REJECTED, "unknown_case")
        self._advance(case, now)
        if envelope.message_id in case.answered_message_ids:
            return ReplyResult(Outcome.DUPLICATE)
        if case.state is CaseState.CLOSED:
            return ReplyResult(Outcome.REJECTED, "case_closed")
        if envelope.sender_agent is not AgentName.WEATHER:
            return ReplyResult(Outcome.REJECTED, "wrong_sender_role")
        if envelope.snapshot_id != case.snapshot_id:
            return ReplyResult(Outcome.REJECTED, "snapshot_mismatch")
        if envelope.valid_until_utc <= now:
            return ReplyResult(Outcome.REJECTED, "expired_reply")
        if case.state is not CaseState.AWAITING_WEATHER:
            return ReplyResult(Outcome.REJECTED, "no_outstanding_request")
        if envelope.reply_to != case.requests[-1].envelope.message_id:
            return ReplyResult(Outcome.REJECTED, "not_the_outstanding_request")
        if signal is WeatherSignal.IMPACT and not assessment_id:
            return ReplyResult(Outcome.REJECTED, "impact_without_assessment_id")

        case.answered_message_ids.add(envelope.message_id)
        case.signal = signal
        case.assessment_id = assessment_id if signal is not WeatherSignal.UNKNOWN else None
        case.timed_out = False
        case.deadline = None
        case.state = CaseState.READY
        return ReplyResult(Outcome.ACCEPTED)

    def poll(self, case_id: str, now: datetime) -> CaseView:
        """Apply timeouts and expiry, then return the current view."""
        _require_utc(now)
        case = self._get(case_id)
        self._advance(case, now)
        return self.view(case_id)

    def _advance(self, case: _Case, now: datetime) -> None:
        if case.state is CaseState.CLOSED:
            return
        if now >= case.valid_until:
            case.state = CaseState.CLOSED
            case.close_reason = CloseReason.EXPIRED
            return
        if case.state is CaseState.AWAITING_WEATHER and case.deadline is not None:
            if now >= case.deadline:
                # Proceed without weather rather than stall; keep any earlier reply.
                case.timed_out = True
                case.deadline = None
                case.state = CaseState.READY

    # -- building and submitting -----------------------------------------------------

    def submit(
        self,
        case_id: str,
        build: Callable[[MessageEnvelope, WeatherSignal, str | None], FlowCandidate],
        now: datetime,
    ) -> FlowCandidate:
        """Build the candidate once per revision; repeated calls return the same one."""
        _require_utc(now)
        case = self._get(case_id)
        self._advance(case, now)
        if case.state is CaseState.CLOSED:
            raise CaseClosedError(f"case {case_id} is closed ({case.close_reason})")
        if case.state is CaseState.AWAITING_WEATHER:
            raise NotReadyError("weather exchange still outstanding")
        if case.state is CaseState.SUBMITTED:
            return case.candidates[-1]

        revision = len(case.candidates) + 1
        envelope = MessageEnvelope(
            schema_version=MessageEnvelope.SUPPORTED_SCHEMA_VERSION,
            message_id=f"{case.case_id}:candidate:{revision}",
            case_id=case.case_id,
            sender_agent=AgentName.CONGESTION,
            created_at_utc=now,
            snapshot_id=case.snapshot_id,
            evidence_ids=(),
            airport_icao="KDTW",
            valid_until_utc=case.valid_until,
            correlation_id=case.case_id,
            reply_to=case.reply_to,
        )
        candidate = build(envelope, case.signal, case.assessment_id)
        check = candidate.envelope
        if (
            check.message_id != envelope.message_id
            or check.case_id != case.case_id
            or check.snapshot_id != case.snapshot_id
        ):
            raise CoordinationError("built candidate does not match the case")
        case.candidates.append(candidate)
        case.state = CaseState.SUBMITTED
        return candidate

    # -- corrections from validation -------------------------------------------------

    def on_correction_request(self, envelope: MessageEnvelope, now: datetime) -> CorrectionResult:
        """Handle validation's request for a reassessment: at most one per case."""
        _require_utc(now)
        case = self._cases.get(envelope.case_id)
        if case is None:
            return CorrectionResult(Outcome.REJECTED, reason="unknown_case")
        self._advance(case, now)
        previous = case.correction_results.get(envelope.message_id)
        if previous is not None:
            return CorrectionResult(Outcome.DUPLICATE, previous.request)
        if case.state is CaseState.CLOSED:
            return CorrectionResult(Outcome.REJECTED, reason="case_closed")
        if envelope.sender_agent is not AgentName.VALIDATION:
            return CorrectionResult(Outcome.REJECTED, reason="wrong_sender_role")
        if envelope.snapshot_id != case.snapshot_id:
            return CorrectionResult(Outcome.REJECTED, reason="snapshot_mismatch")
        if envelope.valid_until_utc <= now:
            return CorrectionResult(Outcome.REJECTED, reason="expired_request")
        if case.state is not CaseState.SUBMITTED:
            return CorrectionResult(Outcome.REJECTED, reason="no_submitted_candidate")
        if envelope.reply_to != case.candidates[-1].envelope.message_id:
            return CorrectionResult(Outcome.REJECTED, reason="not_about_the_latest_candidate")

        if case.corrections >= MAX_CORRECTIONS or case.exchanges >= AssessmentRequest.MAX_ATTEMPT:
            case.state = CaseState.CLOSED
            case.close_reason = CloseReason.PERSISTENT_DISAGREEMENT
            result = CorrectionResult(Outcome.REJECTED, reason="correction_limit_reached")
        else:
            case.corrections += 1
            case.reply_to = envelope.message_id
            result = CorrectionResult(Outcome.ACCEPTED, self._new_request(case, now))
        case.correction_results[envelope.message_id] = result
        return result

    # -- inspection ------------------------------------------------------------------

    def view(self, case_id: str) -> CaseView:
        case = self._get(case_id)
        return CaseView(
            case_id=case.case_id,
            snapshot_id=case.snapshot_id,
            state=case.state,
            exchanges=case.exchanges,
            corrections=case.corrections,
            weather_signal=case.signal,
            weather_assessment_id=case.assessment_id,
            weather_timed_out=case.timed_out,
            candidates_sent=len(case.candidates),
            close_reason=case.close_reason,
        )

    def _get(self, case_id: str) -> _Case:
        try:
            return self._cases[case_id]
        except KeyError:
            raise UnknownCaseError(case_id) from None


def _require_utc(value: datetime) -> None:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be aware UTC datetimes")
