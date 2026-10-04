"""Bounded storage reads and safe failures for durable congestion records."""

import json
from unittest import IsolatedAsyncioTestCase

from shared.settings import SpacetimeSettings
from shared.spacetimedb_adapter import SpacetimeAdapter, SpacetimeError


class StorageReadTests(IsolatedAsyncioTestCase):
    async def test_oversized_reads_explicitly_report_incomplete(self) -> None:
        body = json.dumps(
            [
                {
                    "schema": {"elements": [{"name": {"some": "content_json"}}]},
                    "rows": [["{}"]] * 1001,
                }
            ]
        ).encode()
        adapter = SpacetimeAdapter(
            SpacetimeSettings("http://localhost:3000", "test", "TEST-token"), lambda *args: body
        )
        for table in ("evidence_records", "pending_deliveries"):
            with (
                self.subTest(table=table),
                self.assertRaisesRegex(SpacetimeError, "incomplete read"),
            ):
                await adapter.read_records(table)
        with self.assertRaisesRegex(SpacetimeError, "incomplete read"):
            await adapter.read_observations("aircraft_observations")

    async def test_invalid_storage_response_is_rejected(self) -> None:
        settings = SpacetimeSettings("http://localhost:3000", "test", "TEST-token")
        for result in (
            [{"schema": {"elements": []}, "rows": None}],
            [{"schema": {"elements": [{"name": {"some": "x"}}]}, "rows": [{"x": "unsafe"}]}],
            [
                {
                    "schema": {"elements": [{"name": {"some": "x"}}, {"name": {"some": "x"}}]},
                    "rows": [["a", "b"]],
                }
            ],
        ):
            response = json.dumps(result).encode()
            adapter = SpacetimeAdapter(settings, lambda *args, data=response: data)
            with self.assertRaises(SpacetimeError):
                await adapter.read_records("flow_candidates")

    async def test_ids_are_escaped_and_query_surfaces_allowlisted(self) -> None:
        calls = []

        def transport(url, body, headers, timeout):
            calls.append(body)
            return b'[{"schema":{"elements":[]},"rows":[]}]'

        adapter = SpacetimeAdapter(
            SpacetimeSettings("http://localhost:3000", "test", "TEST-token"), transport
        )
        await adapter.read_records("flow_candidates", key="candidate_id", value="TEST' OR 1=1")
        self.assertEqual(
            calls,
            [b"SELECT * FROM flow_candidates WHERE candidate_id = 'TEST'' OR 1=1' LIMIT 1001"],
        )
        for table, key in (
            ("flow_candidates; DROP", "candidate_id"),
            ("flow_candidates", "status"),
        ):
            with self.assertRaises(ValueError):
                await adapter.read_records(table, key=key, value="TEST")

    async def test_distinct_role_configuration_keeps_tokens_private(self) -> None:
        env = {
            "SPACETIMEDB_HTTP_URL": "http://localhost:3000",
            "SPACETIMEDB_DATABASE": "test",
            "SPACETIMEDB_CONGESTION_TOKEN": "TEST-congestion-token",
            "SPACETIMEDB_INGEST_TOKEN": "TEST-ingest-token",
        }
        settings = SpacetimeSettings.from_env(env, role="congestion")
        self.assertEqual(settings.ingest_token, "TEST-congestion-token")
        self.assertNotIn("TEST-congestion-token", repr(settings))
        with self.assertRaises(ValueError):
            SpacetimeSettings.from_env(env, role="weather")
