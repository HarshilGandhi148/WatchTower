"""Signed Fetch.ai handlers attached to the existing validation agent."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from uagents import Agent, Context, Protocol
from uagents_core.types import DeliveryStatus

from agents.validation.orchestrator import CaseRecord, Dispatch, OrchestrationError, Orchestrator
from shared.contracts import AgentName
from shared.messages import AssessmentRequestMessage, CandidateReadyMessage, CaseOpenedMessage
from shared.orchestration import CandidateReady, ValidationVerdict

ValidateCandidate = Callable[[CandidateReady], Awaitable[ValidationVerdict]]
PersistOutcome = Callable[[CaseRecord], Awaitable[None]]


class ValidationGateway:
    """Bridge routing to SDK transport and required application callbacks.

    validate must read/check the stored evidence, not trust reference IDs alone.
    persist must be idempotent; READY_TO_PUBLISH means pending human review.
    Failed persistence remains queued for retry. There is no default approval.
    """

    def __init__(
        self, coordinator: Orchestrator, validate: ValidateCandidate, persist: PersistOutcome
    ) -> None:
        self.coordinator = coordinator
        self.validate = validate
        self.persist = persist
        self._notification_lock = asyncio.Lock()

    async def handle_open(self, ctx: Context, sender: str, message: CaseOpenedMessage) -> None:
        try:
            dispatch = self.coordinator.open_case(sender, message.envelope.to_envelope())
        except OrchestrationError:
            ctx.logger.warning("Rejected invalid case-open message")
            return
        if dispatch is not None:
            await self._send(ctx, dispatch)
        await self.flush_outcomes(ctx)

    async def handle_candidate(
        self, ctx: Context, sender: str, message: CandidateReadyMessage
    ) -> None:
        try:
            candidate = self.coordinator.receive_candidate(sender, message.to_candidate())
        except ValueError:
            ctx.logger.warning("Rejected invalid candidate message")
            return
        if candidate is None:
            await self.flush_outcomes(ctx)
            return
        case_id = candidate.envelope.case_id
        case = self.coordinator.get_case(case_id)
        remaining = (
            min(case.deadline, candidate.envelope.valid_until_utc) - datetime.now(UTC)
        ).total_seconds()
        try:
            if remaining <= 0:
                raise TimeoutError
            verdict = await asyncio.wait_for(self.validate(candidate), timeout=remaining)
            dispatch = self.coordinator.finish_validation(candidate, verdict)
        except TimeoutError:
            self.coordinator.fail_case(case_id, "validation_timeout")
            dispatch = None
        except Exception:
            # Do not log provider exceptions, credentials, source text or model traces.
            self.coordinator.fail_case(case_id, "validation_unavailable")
            ctx.logger.error("Candidate validation unavailable or no longer current")
            dispatch = None
        if dispatch is not None:
            await self._send(ctx, dispatch)
        await self.flush_outcomes(ctx)

    async def sweep(self, ctx: Context) -> None:
        self.coordinator.expire()
        await self.flush_outcomes(ctx)
        self.coordinator.prune()

    async def flush_outcomes(self, ctx: Context) -> None:
        async with self._notification_lock:
            self.coordinator.expire()
            for case in self.coordinator.pending_notifications():
                try:
                    await asyncio.wait_for(self.persist(case), timeout=5.0)
                except Exception:
                    ctx.logger.error("Case outcome persistence failed; retry remains pending")
                else:
                    self.coordinator.mark_notified(case.opening.case_id)

    async def _send(self, ctx: Context, dispatch: Dispatch) -> None:
        try:
            status = await ctx.send(
                dispatch.recipient, AssessmentRequestMessage.from_request(dispatch.request)
            )
            if status.status == DeliveryStatus.FAILED:
                self.coordinator.fail_case(dispatch.request.envelope.case_id, "delivery_failed")
        except Exception:
            self.coordinator.fail_case(dispatch.request.envelope.case_id, "delivery_failed")
            ctx.logger.error("Assessment dispatch failed")


def register_orchestration(
    agent: Agent,
    coordinator: Orchestrator,
    *,
    validate: ValidateCandidate,
    persist: PersistOutcome,
) -> ValidationGateway:
    """Register handlers on the validator; never create a fourth agent."""
    if agent.address != coordinator.addresses[AgentName.VALIDATION]:
        raise ValueError("Attach orchestration only to the configured validation identity")
    gateway = ValidationGateway(coordinator, validate, persist)
    protocol = Protocol(name="watchtower-coordination", version="1.0")
    protocol.on_message(CaseOpenedMessage, replies={AssessmentRequestMessage})(gateway.handle_open)
    protocol.on_message(CandidateReadyMessage, replies={AssessmentRequestMessage})(
        gateway.handle_candidate
    )
    protocol.on_interval(period=1.0)(gateway.sweep)
    agent.include(protocol, publish_manifest=False)
    return gateway
