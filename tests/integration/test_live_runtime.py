"""Runtime secrets, identities and startup order; no provider calls or real credentials."""

import tempfile
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from runtime.bureau import LocalRegistration
from runtime.live import configured_peers, prepare_runtime, read_settings
from shared.contracts import AgentName


def environment():
    return {
        **{role.value.upper() + "_AGENT_SEED": f"TEST-only-{role}" for role in AgentName},
        **{
            f"SPACETIMEDB_{role.upper()}_TOKEN": f"TEST-{role}-token"
            for role in ("ingest", "weather", "congestion", "validation")
        },
        "SPACETIMEDB_HTTP_URL": "http://127.0.0.1:3000",
        "SPACETIMEDB_DATABASE": "test-live-runtime",
        "DATA_USER_AGENT": "WatchTower TEST",
    }


class SettingsTests(TestCase):
    def test_only_allowlisted_settings_loaded_without_expansion(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text('WEATHER_AGENT_SEED="$(never-execute)"\nADMIN_TOKEN=private\n')
            values = read_settings(path, {"DATA_USER_AGENT": "TEST", "ADMIN_TOKEN": "private"})
        self.assertEqual(
            values, {"WEATHER_AGENT_SEED": "$(never-execute)", "DATA_USER_AGENT": "TEST"}
        )

    def test_three_seed_addresses_are_derived_and_cannot_be_reassigned(self):
        values = configured_peers(environment())
        self.assertEqual(
            len({values[role.value.upper() + "_AGENT_ADDRESS"] for role in AgentName}), 3
        )
        with self.assertRaises(ValueError):
            configured_peers(values | {"WEATHER_AGENT_ADDRESS": values["VALIDATION_AGENT_ADDRESS"]})
        with self.assertRaises(ValueError):
            configured_peers(environment() | {"WEATHER_AGENT_SEED": "TEST-only-validation"})
        with self.assertRaises(ValueError):
            configured_peers(environment() | {"APP_MODE": "test"})


class StartupTests(IsolatedAsyncioTestCase):
    async def test_recovery_finishes_before_three_factories_and_uses_local_registration(self):
        events = []

        async def recover():
            events.append("recovered")

        def factory(*args, **kwargs):
            self.assertEqual(events[0], "recovered")
            events.append("agent")
            return MagicMock()

        with (
            patch("runtime.live.PersistentCongestionService") as service,
            patch("runtime.live.build_weather", side_effect=factory),
            patch("runtime.live.build_congestion", side_effect=factory),
            patch("runtime.live.build_validation", side_effect=factory) as validation,
            patch("runtime.live.build_bureau") as bureau,
        ):
            service.return_value.initialize = AsyncMock(side_effect=recover)
            await prepare_runtime(environment())
        self.assertEqual(events, ["recovered", "agent", "agent", "agent"])
        self.assertFalse(validation.call_args.kwargs["recover_on_start"])
        self.assertTrue(bureau.call_args.kwargs["local_only"])
        policy = LocalRegistration()
        policy.add_agent(MagicMock(), MagicMock())
        await policy.register()
