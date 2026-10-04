"""Polling/failure tests with injected clocks and transports; no live credentials."""

import asyncio
import io
import json
import tempfile
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import HTTPError

from data.ingestion import (
    MAX_BODY,
    FeedError,
    IngestionConfig,
    IngestionService,
    http_get,
    main,
    read_environment,
)
from shared.spacetimedb_adapter import SpacetimeAdapter, SpacetimeError

NOW = datetime(2026, 10, 4, 12, tzinfo=UTC)
FAA = (
    "<AIRPORT_STATUS_INFORMATION><Update_Time>"
    f"{NOW.strftime('%a %b %d %H:%M:%S %Y')} UTC</Update_Time>"
    "</AIRPORT_STATUS_INFORMATION>"
).encode()
ADSB = json.dumps({"now": NOW.timestamp(), "total": 0, "ac": []}).encode()


class PollingTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.now = NOW
        self.adapter = MagicMock(spec=SpacetimeAdapter)
        self.adapter.call = AsyncMock()
        self.adapter.report_source_failure = AsyncMock()
        self.get = MagicMock(side_effect=lambda url, ua: ADSB if "/point/" in url else FAA)
        self.service = IngestionService(
            self.adapter, IngestionConfig(), get=self.get, clock=lambda: self.now, jitter=lambda: 0
        )

    async def test_empty_success_is_stored_with_fetch_and_source_times(self) -> None:
        self.assertTrue(await self.service.poll("adsb_lol"))
        name, payload = self.adapter.call.call_args.args
        self.assertEqual(name, "ingest_feed_batch")
        self.assertEqual(payload["snapshot"]["observations"], ())
        self.assertEqual(payload["snapshot"]["provider_time_utc"], NOW)
        self.assertEqual(payload["snapshot"]["fetched_at_utc"], NOW)
        self.assertIn("surface_coverage_unknown", payload["coverage_flags"])
        self.adapter.report_source_failure.assert_not_awaited()
        self.assertFalse(await self.service.poll("adsb_lol"))
        self.get.assert_called_once()

    async def test_one_provider_failure_does_not_block_other_provider(self) -> None:
        def get(url, ua):
            if "/point/" in url:
                raise FeedError(90)
            return FAA

        self.service.get = get
        results = await asyncio.gather(self.service.poll("adsb_lol"), self.service.poll("faa"))
        self.assertEqual(results, [False, True])
        self.assertEqual(self.service.states["adsb_lol"].due, NOW + timedelta(seconds=90))
        self.assertEqual(self.service.states["faa"].due, NOW + timedelta(seconds=60))
        self.adapter.report_source_failure.assert_awaited_once()
        self.adapter.call.assert_awaited_once()

    async def test_backoff_and_recovery_are_clock_driven(self) -> None:
        self.get.side_effect = OSError("TEST secret must not reach logs")
        self.assertFalse(await self.service.poll("adsb_lol"))
        self.assertEqual(self.service.states["adsb_lol"].due, NOW + timedelta(seconds=20))
        self.now += timedelta(seconds=20)
        self.assertFalse(await self.service.poll("adsb_lol"))
        self.assertEqual(self.service.states["adsb_lol"].due, self.now + timedelta(seconds=40))
        self.now += timedelta(seconds=40)
        self.get.side_effect = None
        self.get.return_value = ADSB
        self.assertTrue(await self.service.poll("adsb_lol"))
        self.assertEqual(self.service.states["adsb_lol"].failures, 0)

    async def test_commit_failure_retries_same_batch_without_refetching(self) -> None:
        self.adapter.call.side_effect = [SpacetimeError("TEST response lost"), None]
        self.assertFalse(await self.service.poll("adsb_lol"))
        first = self.adapter.call.call_args.args
        self.adapter.report_source_failure.assert_not_awaited()
        self.now += timedelta(seconds=20)
        self.assertTrue(await self.service.poll("adsb_lol"))
        self.assertEqual(first, self.adapter.call.call_args.args)
        self.get.assert_called_once()

    async def test_failed_commit_logs_category_without_private_details(self) -> None:
        self.adapter.call.side_effect = SpacetimeError("TEST-private-token-and-response")
        with self.assertLogs("watchtower.ingestion", level="WARNING") as logs:
            self.assertFalse(await self.service.poll("adsb_lol"))
        self.assertIn("database commit", logs.output[0])
        self.assertNotIn("TEST-private", " ".join(logs.output))

    async def test_malformed_and_oversized_inputs_are_not_empty_successes(self) -> None:
        self.get.side_effect = None
        for body in (b"{}", b"<html>error</html>", b"not json"):
            self.get.return_value = body
            self.service.states["adsb_lol"].due = None
            self.assertFalse(await self.service.poll("adsb_lol"))
        self.adapter.call.assert_not_awaited()
        self.assertEqual(self.adapter.report_source_failure.await_count, 3)

    async def test_rejected_records_and_unknown_sections_are_saved(self) -> None:
        self.get.side_effect = None
        self.get.return_value = json.dumps(
            {"now": NOW.timestamp(), "total": 1, "ac": [{"hex": "bad"}]}
        ).encode()
        await self.service.poll("adsb_lol")
        payload = self.adapter.call.call_args.args[1]
        self.assertEqual(payload["snapshot"]["rejected_records"], 1)
        self.assertIn("rejected_records", payload["coverage_flags"])
        self.get.return_value = FAA.replace(
            b"</AIRPORT_STATUS_INFORMATION>",
            b"<Delay_type><Name>TEST unknown</Name></Delay_type></AIRPORT_STATUS_INFORMATION>",
        )
        await self.service.poll("faa")
        self.assertIn("unknown_sections", self.adapter.call.call_args.args[1]["coverage_flags"])

    async def test_concurrent_same_provider_polls_fetch_once(self) -> None:
        await asyncio.gather(*(self.service.poll("adsb_lol") for _ in range(4)))
        self.adapter.call.assert_awaited_once()
        self.get.assert_called_once()


class LaunchTests(TestCase):
    def test_explicit_file_process_precedence_and_no_shell_expansion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "\ufeff# TEST only\nSPACETIMEDB_DATABASE=watchtower-test\n"
                'SPACETIMEDB_INGEST_TOKEN="TEST-$UNCHANGED"\n'
                "WEATHER_AGENT_SEED=TEST-not-for-ingestion\n",
                encoding="utf-8",
            )
            values = read_environment(path, {"SPACETIMEDB_DATABASE": "watchtower-other"})
        self.assertEqual(values["SPACETIMEDB_DATABASE"], "watchtower-other")
        self.assertEqual(values["SPACETIMEDB_INGEST_TOKEN"], "TEST-$UNCHANGED")
        self.assertNotIn("WEATHER_AGENT_SEED", values)
        self.assertEqual(read_environment(None, {}), {})

    def test_selected_provider_and_application_identifier_reach_service(self) -> None:
        environment = {
            "SPACETIMEDB_HTTP_URL": "http://127.0.0.1:3000",
            "SPACETIMEDB_DATABASE": "watchtower-test",
            "SPACETIMEDB_INGEST_TOKEN": "TEST-token",
            "DATA_USER_AGENT": "<team-placeholder>",
        }
        service = MagicMock()
        service.run_provider = AsyncMock()
        with (
            patch("data.ingestion.os.environ", environment),
            patch("data.ingestion.IngestionService", return_value=service) as factory,
        ):
            main(["--provider", "adsb_lol", "--user-agent", "TEST application"])
        service.run_provider.assert_awaited_once_with("adsb_lol")
        self.assertEqual(factory.call_args.args[1].user_agent, "TEST application")

    def test_invalid_and_nonlive_settings_fail_without_printing_secrets(self) -> None:
        for values in (
            {"SPACETIMEDB_INGEST_TOKEN": "TEST-secret-token"},
            {"APP_MODE": "test", "SPACETIMEDB_INGEST_TOKEN": "TEST-secret-token"},
        ):
            with (
                self.subTest(values=values),
                patch("data.ingestion.os.environ", values),
                patch("sys.stderr", new_callable=io.StringIO) as errors,
                self.assertRaises(SystemExit),
            ):
                main([])
            self.assertNotIn("TEST-secret-token", errors.getvalue())

    def test_malformed_environment_file_fails_without_echoing_content(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("TEST-secret-content-without-an-assignment\n", encoding="utf-8")
            with (
                patch("data.ingestion.os.environ", {}),
                patch("sys.stderr", new_callable=io.StringIO) as errors,
                self.assertRaises(SystemExit),
            ):
                main(["--env-file", str(path)])
            self.assertNotIn("TEST-secret-content", errors.getvalue())


class HttpTests(TestCase):
    def test_http_error_is_redacted_and_retry_after_is_bounded(self) -> None:
        headers = Message()
        headers["Retry-After"] = "999"
        headers["Authorization"] = "TEST credential"
        error = HTTPError(
            "https://example.test/private?token=TEST",
            429,
            "TEST sensitive",
            headers,
            io.BytesIO(b"TEST sensitive body"),
        )
        opener = MagicMock()
        opener.open.side_effect = error
        with (
            patch("data.ingestion.build_opener", return_value=opener),
            self.assertRaises(FeedError) as caught,
        ):
            http_get("https://example.test", "TEST user agent")
        self.assertEqual(str(caught.exception), "provider fetch failed")
        self.assertEqual(caught.exception.retry_after_s, 300)
        self.assertIsNone(caught.exception.__cause__)

    def test_oversized_body_and_redirects_fail_safely(self) -> None:
        opener = MagicMock()
        opener.open.return_value.__enter__.return_value.read.return_value = b"x" * (MAX_BODY + 1)
        with (
            patch("data.ingestion.build_opener", return_value=opener),
            self.assertRaises(FeedError),
        ):
            http_get("https://example.test", "TEST user agent")
        self.assertEqual(opener.open.call_args.kwargs["timeout"], 10)
        with self.assertRaises(FeedError):
            http_get("http://example.test", "TEST user agent")

    def test_configuration_uses_same_center_and_radius(self) -> None:
        config = IngestionConfig.from_env(
            {
                "DATA_USER_AGENT": "TEST team",
                "TRAFFIC_RADIUS_NM": "25",
                "AIRPORT_LATITUDE_DEG": "42.3",
            }
        )
        self.assertIn("/42.3000/-83.3534/25", config.url("adsb_lol"))
        with self.assertRaises(ValueError):
            IngestionConfig.from_env({"DATA_USER_AGENT": "<team>"})

    def test_http_date_retry_after_uses_public_delay_only(self) -> None:
        headers = Message()
        headers["Retry-After"] = "Sun, 04 Oct 2026 12:01:30 GMT"
        error = HTTPError("https://example.test", 429, "TEST", headers, io.BytesIO())
        opener = MagicMock()
        opener.open.side_effect = error
        with (
            patch("data.ingestion.build_opener", return_value=opener),
            patch("data.ingestion.datetime") as clock,
        ):
            clock.now.return_value = NOW
            with self.assertRaises(FeedError) as caught:
                http_get("https://example.test", "TEST team")
        self.assertEqual(caught.exception.retry_after_s, 90)
