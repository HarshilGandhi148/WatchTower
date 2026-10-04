"""Validation agent factory: configuration, protocol wiring and a stored-case round trip.

SDK ledger/almanac services are mocked; no server, database, provider or model is used.
"""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from uagents_core.contrib.protocols.chat import ChatMessage
from uagents_core.types import DeliveryStatus

from agents.validation.agent import AgentConfig, ValidationCallbacks, build_agent
from agents.validation.ledger import AdvisoryLedger
from agents.validation.orchestrator import Orchestrator
from agents.validation.packet import ValidationConfig
from agents.validation.protocol import ValidationGateway
from agents.validation.storage import ValidationStorage
from shared.contracts import AdvisoryStatus, AgentName
from shared.messages import (
    AssessmentRequestMessage,
    CandidateReadyMessage,
    CaseOpenedMessage,
    EnvelopeModel,
)
from shared.orchestration import ValidationVerdict
from shared.persistence import ready_from_dict
from tests.fixtures.orchestration import TEST_ADDRESSES
from tests.fixtures.validation import storage as fs

ENV = {
    "VALIDATION_AGENT_SEED": "watchtower TEST ONLY validation identity",
    "WEATHER_AGENT_ADDRESS": TEST_ADDRESSES[AgentName.WEATHER],
    "CONGESTION_AGENT_ADDRESS": TEST_ADDRESSES[AgentName.CONGESTION],
    "SPACETIMEDB_HTTP_URL": "http://127.0.0.1:3000",
    "SPACETIMEDB_DATABASE": "watchtower-test",
    "SPACETIMEDB_VALIDATION_TOKEN": "TEST-token",
}


class ConfigTests(TestCase):
    def test_required_names_are_reported_without_values(self) -> None:
        for name in ("VALIDATION_AGENT_SEED", "WEATHER_AGENT_ADDRESS", "CONGESTION_AGENT_ADDRESS"):
            with self.subTest(name=name), self.assertRaises(ValueError) as raised:
                AgentConfig.from_env({**ENV, name: " "})
            self.assertIn(name, str(raised.exception))
        with self.assertRaises(ValueError):
            AgentConfig.from_env({**ENV, "SPACETIMEDB_VALIDATION_TOKEN": ""})

    def test_chat_model_is_optional_and_secrets_stay_out_of_repr(self) -> None:
        config = AgentConfig.from_env(ENV)
        self.assertFalse(config.llm.enabled)
        keyed = AgentConfig.from_env({**ENV, "ASI_ONE_API_KEY": "TEST-key"})
        self.assertTrue(keyed.llm.enabled)
        text = repr(keyed)
        for secret in ("TEST ONLY validation identity", "TEST-key", "TEST-token"):
            self.assertNotIn(secret, text)

    def test_standalone_port(self) -> None:
        self.assertEqual(AgentConfig.from_env(ENV).port, 8002)
        self.assertEqual(AgentConfig.from_env({**ENV, "VALIDATION_AGENT_PORT": "8010"}).port, 8010)
        for bad in ("0", "70000", "abc", "-1"):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                AgentConfig.from_env({**ENV, "VALIDATION_AGENT_PORT": bad})

    def test_main_reports_missing_configuration_without_starting(self) -> None:
        from agents.validation import agent as module

        with patch.dict("os.environ", {}, clear=True), self.assertRaises(SystemExit) as raised:
            module.main()
        self.assertIn("VALIDATION_AGENT_SEED", str(raised.exception))


class FactoryTests(TestCase):
    def setUp(self) -> None:
        for target in ("uagents.agent.get_ledger", "uagents.agent.get_almanac_contract"):
            patcher = patch(target, return_value=MagicMock() if "ledger" in target else None)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)

    def tearDown(self) -> None:
        self.loop.close()
        asyncio.set_event_loop(None)

    def test_agent_has_coordination_and_chat_protocols(self) -> None:
        config = replace(AgentConfig.from_env(ENV), mailbox=False)
        agent = build_agent(config, adapter=fs.FakeStore(), loop=self.loop)  # type: ignore[arg-type]
        protocols = {p.name: p for p in agent.protocols.values()}
        coordination = protocols["watchtower-coordination"]
        self.assertEqual(len(coordination.signed_message_handlers), 2)
        self.assertEqual(len(coordination.intervals), 1)
        chat = next(p for p in agent.protocols.values() if ChatMessage in p.models.values())
        self.assertIsNot(chat, coordination)

    def test_mailbox_identity_for_asi_one(self) -> None:
        agent = build_agent(AgentConfig.from_env(ENV), adapter=fs.FakeStore(), loop=self.loop)  # type: ignore[arg-type]
        self.assertTrue(agent.address.startswith("agent1"))


class RoundTripTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.at = fs.at_now()
        self.store, self.opening = fs.stored_case(self.at)
        self.ledger = AdvisoryLedger()
        coordinator = Orchestrator(TEST_ADDRESSES)
        storage = ValidationStorage(self.store)  # type: ignore[arg-type]
        self.callbacks = ValidationCallbacks(
            coordinator,
            self.store,  # type: ignore[arg-type]
            storage,
            self.ledger,
            ValidationConfig(),
            lambda: datetime.now(UTC),
            MagicMock(),
        )
        self.gateway = ValidationGateway(
            coordinator, self.callbacks.validate, self.callbacks.persist, storage
        )
        self.ctx = MagicMock()
        self.ctx.send = AsyncMock(return_value=MagicMock(status=DeliveryStatus.DELIVERED))

    async def run_case(self, tamper: bool = False) -> None:
        congestion = TEST_ADDRESSES[AgentName.CONGESTION]
        opened = CaseOpenedMessage(envelope=EnvelopeModel.from_envelope(self.opening))
        await self.gateway.handle_open(self.ctx, congestion, opened)
        sent = self.ctx.send.await_args.args[1]
        assert isinstance(sent, AssessmentRequestMessage)
        candidate, ready = fs.candidate_for(sent.to_request(), self.at)
        if tamper:
            metrics = tuple(
                replace(m, value=m.value + 1) if m.name == "projected_total_waiting_change" else m
                for m in candidate.traffic_metrics
            )
            candidate = replace(candidate, traffic_metrics=metrics)
        fs.store_candidate(self.store, candidate, ready)
        # Congestion sends the delivery as read back from storage (millisecond times).
        stored = self.store.tables["pending_deliveries"][ready.candidate_id]["content_json"]
        ready = ready_from_dict(json.loads(stored))
        message = CandidateReadyMessage(
            envelope=EnvelopeModel.from_envelope(ready.envelope),
            candidate_id=ready.candidate_id,
            weather_assessment_id=ready.weather_assessment_id,
            assessment_round=ready.assessment_round,
            projection_id=ready.projection_id,
        )
        await self.gateway.handle_candidate(self.ctx, congestion, message)

    async def test_stored_candidate_is_published_for_review(self) -> None:
        await self.run_case()
        (advisory,) = self.ledger.pending(datetime.now(UTC))
        self.assertEqual((advisory.case_id, advisory.version), (fs.CASE_ID, 1))
        self.assertIs(advisory.status, AdvisoryStatus.PENDING_REVIEW)
        self.assertIn(fs.PROJECTION_ID, advisory.evidence_ids)
        receipts = [c for c in self.store.calls if c[0] == "record_candidate_receipt"]
        self.assertEqual(len(receipts), 1)
        facts = self.ledger.facts(datetime.now(UTC))
        self.assertEqual(facts["pending_advisories"][0]["advisory_id"], advisory.advisory_id)

    async def test_rejected_candidate_stays_internal(self) -> None:
        await self.run_case(tamper=True)
        self.assertEqual(self.ledger.pending(datetime.now(UTC)), ())
        (result,) = self.ledger.recent_results()
        self.assertIs(result.verdict, ValidationVerdict.REJECT)
        self.assertIn("projected_value_mismatch", result.finding_codes)

    async def test_persisted_outcome_is_not_published_twice(self) -> None:
        await self.run_case()
        case = self.gateway.coordinator.get_case(fs.CASE_ID)
        await self.callbacks.persist(case)
        self.assertEqual(len(self.ledger.pending(datetime.now(UTC))), 1)
        self.assertEqual(
            json.loads(json.dumps(self.ledger.facts(datetime.now(UTC))))["airport"], "KDTW"
        )
