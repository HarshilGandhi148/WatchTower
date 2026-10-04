"""Real SDK messages and mocked transport; no database or model credentials."""

from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from uagents import Context
from uagents_core.types import DeliveryStatus, MsgStatus

from agents.validation.orchestrator import Orchestrator
from agents.validation.protocol import ValidationGateway
from shared.contracts import AgentName
from shared.messages import CandidateReadyMessage, CaseOpenedMessage, EnvelopeModel
from shared.orchestration import CasePhase, ValidationVerdict
from tests.fixtures.orchestration import TEST_ADDRESSES, candidate, opening


class ValidationGatewayTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.coordinator = Orchestrator(TEST_ADDRESSES)
        self.validate = AsyncMock(return_value=ValidationVerdict.PUBLISH)
        self.persist = AsyncMock()
        self.gateway = ValidationGateway(self.coordinator, self.validate, self.persist)
        self.ctx = MagicMock(spec=Context)
        self.ctx.send = AsyncMock(
            return_value=MsgStatus(
                status=DeliveryStatus.DELIVERED,
                detail="test delivered",
                destination=TEST_ADDRESSES[AgentName.CONGESTION],
                endpoint="test-local",
            )
        )
        self.opening = opening()
        self.message = CaseOpenedMessage(envelope=EnvelopeModel.from_envelope(self.opening))

    def prepare_candidate(self) -> CandidateReadyMessage:
        dispatch = self.coordinator.open_case(TEST_ADDRESSES[AgentName.WEATHER], self.opening)
        assert dispatch is not None
        packet = candidate(dispatch)
        return CandidateReadyMessage(
            envelope=EnvelopeModel.from_envelope(packet.envelope),
            candidate_id=packet.candidate_id,
            weather_assessment_id=packet.weather_assessment_id,
            assessment_round=packet.assessment_round,
        )

    async def test_open_routes_one_typed_request_to_congestion(self) -> None:
        await self.gateway.handle_open(self.ctx, TEST_ADDRESSES[AgentName.WEATHER], self.message)
        await self.gateway.handle_open(self.ctx, TEST_ADDRESSES[AgentName.WEATHER], self.message)
        self.ctx.send.assert_awaited_once()
        recipient, wire = self.ctx.send.call_args.args
        self.assertEqual(recipient, TEST_ADDRESSES[AgentName.CONGESTION])
        self.assertEqual(wire.to_request().envelope.snapshot_id, self.opening.snapshot_id)

    async def test_unassigned_sender_cannot_trigger_requests_or_validation(self) -> None:
        await self.gateway.handle_open(self.ctx, "test-unassigned-address", self.message)
        self.ctx.send.assert_not_awaited()
        self.validate.assert_not_awaited()

    async def test_transport_failure_records_insufficient_data(self) -> None:
        self.ctx.send.return_value.status = DeliveryStatus.FAILED
        await self.gateway.handle_open(self.ctx, TEST_ADDRESSES[AgentName.WEATHER], self.message)
        outcome = self.persist.call_args.args[0]
        self.assertEqual(outcome.phase, CasePhase.INSUFFICIENT_DATA)
        self.assertEqual(outcome.reason, "delivery_failed")

    async def test_candidate_is_validated_and_persisted_once(self) -> None:
        message = self.prepare_candidate()
        for _ in range(2):
            await self.gateway.handle_candidate(
                self.ctx, TEST_ADDRESSES[AgentName.CONGESTION], message
            )
        self.validate.assert_awaited_once()
        self.persist.assert_awaited_once()
        self.assertEqual(self.persist.call_args.args[0].phase, CasePhase.READY_TO_PUBLISH)

    async def test_validation_failure_closes_case_without_publishing(self) -> None:
        message = self.prepare_candidate()
        self.validate.side_effect = RuntimeError("test model unavailable")
        await self.gateway.handle_candidate(self.ctx, TEST_ADDRESSES[AgentName.CONGESTION], message)
        self.assertEqual(self.persist.call_args.args[0].phase, CasePhase.INSUFFICIENT_DATA)
        self.assertEqual(self.persist.call_args.args[0].reason, "validation_unavailable")

    async def test_reassessment_sends_round_two_with_same_evidence(self) -> None:
        message = self.prepare_candidate()
        self.validate.return_value = ValidationVerdict.REASSESS
        await self.gateway.handle_candidate(self.ctx, TEST_ADDRESSES[AgentName.CONGESTION], message)
        recipient, wire = self.ctx.send.call_args.args
        self.assertEqual(recipient, TEST_ADDRESSES[AgentName.CONGESTION])
        self.assertEqual(wire.assessment_round, 2)
        self.assertEqual(wire.envelope.to_envelope().evidence_ids, self.opening.evidence_ids)
        self.persist.assert_not_awaited()

    async def test_failed_persistence_retries_without_revalidating(self) -> None:
        message = self.prepare_candidate()
        self.persist.side_effect = [RuntimeError("test database unavailable"), None]
        await self.gateway.handle_candidate(self.ctx, TEST_ADDRESSES[AgentName.CONGESTION], message)
        self.assertEqual(len(self.coordinator.pending_notifications()), 1)
        await self.gateway.flush_outcomes(self.ctx)
        self.assertEqual(self.coordinator.pending_notifications(), ())
        self.assertEqual(self.persist.await_count, 2)
        self.validate.assert_awaited_once()

    async def test_timeout_persists_internal_notice(self) -> None:
        self.prepare_candidate()
        deadline = self.coordinator.get_case(self.opening.case_id).deadline
        self.coordinator.expire(now=deadline)
        await self.gateway.sweep(self.ctx)
        self.assertEqual(self.persist.call_args.args[0].phase, CasePhase.INSUFFICIENT_DATA)
        self.assertEqual(self.persist.call_args.args[0].reason, "timeout")
