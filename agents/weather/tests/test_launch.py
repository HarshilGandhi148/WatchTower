"""Launcher configuration and real-provider read scheduling without live fixtures."""

import asyncio
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch

from agents.weather.launch import LiveWeatherReader, build_inspector_agent, main, read_settings
from agents.weather.tests.helpers import NOW, snapshot
from data.awc import AwcError, FetchResult


class ReaderTests(IsolatedAsyncioTestCase):
    async def test_independent_refresh_and_snapshot_provenance(self) -> None:
        now = NOW
        snap = snapshot()
        client = MagicMock()

        async def fetch(product):  # type: ignore[no-untyped-def]
            return FetchResult(product, now, snap.metars if product == "metar" else snap.tafs)

        client.fetch = AsyncMock(side_effect=fetch)
        reader = LiveWeatherReader(client, clock=lambda: now)
        first, second = await asyncio.gather(reader.snapshot(), reader.snapshot())
        self.assertEqual(client.fetch.await_count, 2)
        self.assertNotEqual(first.snapshot_id, second.snapshot_id)
        self.assertEqual(first.evidence_ids, snap.evidence_ids)
        now += timedelta(seconds=60)
        await reader.snapshot()
        self.assertEqual(client.fetch.await_count, 3)
        self.assertEqual(client.fetch.call_args.args[0], "metar")
        now += timedelta(seconds=240)
        await reader.snapshot()
        self.assertEqual(client.fetch.await_count, 5)

    async def test_failure_is_missing_and_retry_after_is_respected(self) -> None:
        now = NOW
        client = MagicMock()
        client.fetch = AsyncMock(side_effect=AwcError("rate limited", 600))
        reader = LiveWeatherReader(client, clock=lambda: now)
        result = await reader.snapshot()
        self.assertEqual(result.evidence_ids, ())
        now += timedelta(seconds=300)
        await reader.snapshot()
        self.assertEqual(client.fetch.await_count, 2)
        now += timedelta(seconds=300)
        await reader.snapshot()
        self.assertEqual(client.fetch.await_count, 4)


class SettingsTests(TestCase):
    def test_explicit_env_file_allowlist_and_process_override(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text('WEATHER_AGENT_SEED="private value"\nASI_ONE_API_KEY=key\nOTHER=x\n')
            values = read_settings(path, {"ASI_ONE_API_KEY": "override"})
        self.assertEqual(values["WEATHER_AGENT_SEED"], "private value")
        self.assertEqual(values["ASI_ONE_API_KEY"], "override")
        self.assertNotIn("OTHER", values)

    def test_invalid_env_and_port_errors_never_print_secret(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / ".env"
            path.write_text("private invalid secret line")
            with self.assertRaisesRegex(ValueError, "NAME=value"):
                read_settings(path, {})
        with self.assertRaises(ValueError):
            build_inspector_agent({}, port=0)

    def test_cli_starts_loopback_server_without_reading_env_implicitly(self) -> None:
        with (
            patch("agents.weather.launch.os.environ", {}),
            patch("agents.weather.launch.build_inspector_agent") as build,
            patch("agents.weather.launch.uagents.asgi.HOST"),
            patch("builtins.print"),
        ):
            main(["--port", "8123"])
            build.assert_called_once_with({}, port=8123, mailbox=False)
            build.return_value.run.assert_called_once()


class InspectorTests(IsolatedAsyncioTestCase):
    async def test_sdk_inspector_and_signed_weather_chat_registered(self) -> None:
        with (
            patch("uagents.agent.get_ledger", return_value=MagicMock()),
            patch("uagents.agent.get_almanac_contract", return_value=None),
        ):
            agent = build_inspector_agent({"WEATHER_AGENT_SEED": "TEST-only weather inspector"})
        self.assertEqual(agent.name, "weather")
        self.assertTrue(agent._enable_agent_inspector)
        self.assertFalse(agent._use_mailbox)
        self.assertIsNone(agent._message_history._storage)
        self.assertTrue(agent._server.has_rest_endpoint("GET", "/agent_info"))
        self.assertTrue(agent._server.has_rest_endpoint("GET", "/messages"))
