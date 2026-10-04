import asyncio
import json
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from runtime.bridge_listener import Dispatcher
from shared.bridge import StreamEvent, fresh
from shared.settings import SpacetimeSettings
from shared.spacetimedb_adapter import SpacetimeError
from watchtower_client import Client


class ClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_transient_retry_and_redacted_authorization(self):
        calls = []

        def transport(url, body, headers, timeout):
            calls.append(body)
            if len(calls) == 1:
                raise SpacetimeError("connection or timeout")
            return b""

        client = Client(
            SpacetimeSettings("http://127.0.0.1:3000", "test", "TEST-token"),
            transport=transport,
            backoff=0,
        )
        await client.call("write_ingest_event", "stable", 0, '{"schema_version":1}')
        self.assertEqual(calls[0], calls[1])
        self.assertEqual(len(calls), 2)

        def denied(*args):
            raise SpacetimeError("authorization", 403)

        client.transport = denied
        with self.assertRaises(SpacetimeError):
            await client.call("write_ingest_event", "stable", 0, "{}")

    async def test_sql_decoder_and_malformed(self):
        client = Client(
            SpacetimeSettings("http://localhost:3000", "test", "TEST-token"),
            transport=lambda *args: json.dumps(
                [{"schema": {"elements": [{"name": {"some": "id"}}]}, "rows": [[1]]}]
            ).encode(),
        )
        self.assertEqual(await client.sql("SELECT * FROM ingest_event"), [{"id": 1}])
        client.transport = lambda *args: b"invalid"
        with self.assertRaises(SpacetimeError):
            await client.sql("SELECT * FROM ingest_event")
        with self.assertRaises(ValueError):
            await client.sql("DELETE FROM ingest_event")

    async def test_routing_retry_restart_and_concurrency(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / "receipts.db")
            dispatcher = Dispatcher(path)
            calls = []
            failed = True

            async def good(event):
                await asyncio.sleep(0)
                calls.append("weather")

            async def flaky(event):
                nonlocal failed
                if failed:
                    failed = False
                    raise RuntimeError("controlled TEST failure")
                calls.append("congestion")

            dispatcher.register("ingest_event", "weather", good)
            dispatcher.register("ingest_event", "congestion", flaky)
            dispatcher.register("validation_result", "other", good)
            event = StreamEvent("ingest_event", 1, 0, datetime.now(UTC).isoformat(), {})
            with self.assertRaises(RuntimeError):
                await dispatcher.dispatch(event)
            await dispatcher.dispatch(event)
            self.assertEqual(calls, ["weather", "congestion"])
            dispatcher.db.close()
            dispatcher = Dispatcher(path)
            dispatcher.register("ingest_event", "weather", good)
            await asyncio.gather(dispatcher.dispatch(event), dispatcher.dispatch(event))
            self.assertEqual(calls, ["weather", "congestion"])
            fresh_event = StreamEvent("ingest_event", 2, 0, event.created_at_utc, {})
            await asyncio.gather(dispatcher.dispatch(fresh_event), dispatcher.dispatch(fresh_event))
            self.assertEqual(calls, ["weather", "congestion", "weather"])
            dispatcher.db.close()

    def test_freshness(self):
        now = datetime.now(UTC)
        self.assertTrue(fresh(now.isoformat(), now))
        for value in (
            None,
            "invalid",
            now.replace(tzinfo=None).isoformat(),
            (now - timedelta(hours=1)).isoformat(),
            (now + timedelta(hours=1)).isoformat(),
        ):
            self.assertFalse(fresh(value, now))
