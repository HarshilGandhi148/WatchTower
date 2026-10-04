"""SDK registration smoke tests; do not start servers or contact providers."""

import asyncio
from unittest import TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from uagents import Agent

from agents.validation.orchestrator import Orchestrator
from agents.validation.protocol import register_orchestration
from runtime.bureau import build_bureau, build_coordinated_bureau
from shared.contracts import AgentName
from shared.orchestration import ValidationVerdict


class BureauWiringTests(TestCase):
    def setUp(self) -> None:
        # SDK constructors initialize ledger services; keep those external services
        # mocked while testing real Agent identities, models and Protocol registration.
        ledger = patch("uagents.agent.get_ledger", return_value=MagicMock())
        contract = patch("uagents.agent.get_almanac_contract", return_value=None)
        ledger.start()
        contract.start()
        self.addCleanup(ledger.stop)
        self.addCleanup(contract.stop)
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.agents = {
            role: Agent(
                name=f"test-{role.value}",
                seed=f"watchtower TEST ONLY {role.value} identity",
                loop=self.loop,
                enable_agent_inspector=False,
                publish_agent_details=False,
                report_events=False,
            )
            for role in AgentName
        }
        self.validate = AsyncMock(return_value=ValidationVerdict.INSUFFICIENT_DATA)
        self.persist = AsyncMock()

    def tearDown(self) -> None:
        self.loop.close()
        asyncio.set_event_loop(None)

    def test_real_bureau_has_three_agents_and_validation_has_signed_protocol(self) -> None:
        bureau = build_coordinated_bureau(self.agents, validate=self.validate, persist=self.persist)
        # The pinned SDK exposes its member list only as _agents.
        self.assertEqual(len(bureau._agents), 3)
        protocols = self.agents[AgentName.VALIDATION].protocols.values()
        protocol = next(p for p in protocols if p.name == "watchtower-coordination")
        self.assertEqual(len(protocol.signed_message_handlers), 2)
        self.assertEqual(len(protocol.unsigned_message_handlers), 0)
        self.assertEqual(len(protocol.intervals), 1)

    def test_missing_or_reused_identity_is_rejected(self) -> None:
        for agents in (
            {AgentName.WEATHER: self.agents[AgentName.WEATHER]},
            {role: self.agents[AgentName.WEATHER] for role in AgentName},
        ):
            with self.subTest(agents=agents), self.assertRaises(ValueError):
                build_bureau(agents)

    def test_orchestration_cannot_be_attached_to_a_specialist(self) -> None:
        coordinator = Orchestrator({role: agent.address for role, agent in self.agents.items()})
        with self.assertRaises(ValueError):
            register_orchestration(
                self.agents[AgentName.WEATHER],
                coordinator,
                validate=self.validate,
                persist=self.persist,
            )
