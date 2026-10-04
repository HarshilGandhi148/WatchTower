"""Congestion agent factory: required configuration and SDK registration (no server started)."""

import asyncio
from unittest import TestCase
from unittest.mock import MagicMock, patch

from agents.congestion.agent import AgentConfig, build_agent
from shared.messages import AssessmentRequestMessage, WeatherAssessmentReplyMessage

ENV = {
    "CONGESTION_AGENT_SEED": "watchtower TEST ONLY congestion identity",
    "WEATHER_AGENT_ADDRESS": "test-weather-address",
    "VALIDATION_AGENT_ADDRESS": "test-validation-address",
    "SPACETIMEDB_HTTP_URL": "http://127.0.0.1:3000",
    "SPACETIMEDB_DATABASE": "watchtower-test",
    "SPACETIMEDB_CONGESTION_TOKEN": "test-token",
    "DATA_USER_AGENT": "WatchTower-tests (TEST)",
}


class ConfigTests(TestCase):
    def test_required_values_are_never_defaulted(self) -> None:
        for name in ("CONGESTION_AGENT_SEED", "WEATHER_AGENT_ADDRESS", "VALIDATION_AGENT_ADDRESS"):
            with self.subTest(missing=name), self.assertRaises(ValueError) as caught:
                AgentConfig.from_env({**ENV, name: " "})
            self.assertIn(name, str(caught.exception))
            self.assertNotIn(ENV["CONGESTION_AGENT_SEED"], str(caught.exception))

    def test_invalid_airport_config_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            AgentConfig.from_env({**ENV, "AIRPORT_LATITUDE_DEG": "200"})

    def test_feed_settings_come_from_the_environment(self) -> None:
        config = AgentConfig.from_env({**ENV, "TRAFFIC_RADIUS_NM": "25"})
        self.assertEqual(config.airport.radius_nm, 25)
        self.assertEqual(config.weather_address, "test-weather-address")


class FactoryTests(TestCase):
    def setUp(self) -> None:
        # Same approach as tests/integration/test_bureau_wiring.py: mock ledger services.
        for target, value in (
            ("uagents.agent.get_ledger", MagicMock()),
            ("uagents.agent.get_almanac_contract", None),
        ):
            patcher = patch(target, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.addCleanup(asyncio.set_event_loop, None)
        self.addCleanup(self.loop.close)

    def build(self):  # type: ignore[no-untyped-def]
        return build_agent(AgentConfig.from_env(ENV), loop=self.loop)

    def test_identity_is_stable_for_a_seed(self) -> None:
        self.assertEqual(self.build().address, self.build().address)

    def test_handlers_are_registered_for_the_protocol_messages(self) -> None:
        agent = self.build()
        registered = {
            model.__name__
            for protocol in [agent._protocol, *agent.protocols.values()]  # pinned SDK internals
            for model in protocol.models.values()
        }
        self.assertIn(AssessmentRequestMessage.__name__, registered)
        self.assertIn(WeatherAssessmentReplyMessage.__name__, registered)
