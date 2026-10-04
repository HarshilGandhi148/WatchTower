"""A stored scalar METAR remains partial evidence, with durable round binding."""

import json
from dataclasses import replace
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agents.weather.persistence import WeatherStorage
from agents.weather.service import WeatherDependencies, WeatherService
from agents.weather.tests.helpers import NOW, request, snapshot
from data.weather_ingestion import normalized_metar
from shared.contracts import WeatherStatus
from shared.persistence import storage_json


class PersistenceTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = AsyncMock()
        self.record = {
            "provider": "awc",
            "observed_at_utc": NOW.isoformat(),
            "fetched_at_utc": NOW.isoformat(),
            "source_ref": "https://example.test/metar",
            "content_json": json.dumps(
                [
                    "https://example.test/metar",
                    json.dumps(
                        {
                            "windSpeedKt": 5,
                            "windDirectionDeg": 180,
                            "visibilityM": 16093.44,
                            "ceilingFt": None,
                        }
                    ),
                ]
            ),
        }
        self.adapter.load_snapshot.return_value = {
            "cutoff_at_utc": NOW.isoformat(),
            "evidence_ids": ["snap-1:weather:awc"],
            "weather": [self.record],
        }
        self.storage = WeatherStorage(self.adapter)

    async def test_real_service_persists_partial_finding_and_retry_is_identical(self):
        snapshot = await self.storage.load_snapshot("snap-1")
        self.assertFalse(snapshot.metars[0].conditions.clear_skies)
        self.assertFalse(snapshot.metars[0].conditions.weather_known)
        self.assertEqual(snapshot.tafs, ())
        service = WeatherService(
            WeatherDependencies(self.storage.load_snapshot, self.storage.persist_assessment),
            clock=lambda: NOW,
        )
        req = request(snapshot, message_id="case-1:weather-request:1")
        reply = await service.handle(req)
        self.assertEqual(reply.status, WeatherStatus.INSUFFICIENT_DATA)
        self.assertEqual(await service.handle(req), reply)
        finding = self.adapter.save_weather.call_args.args[0]
        self.assertEqual(finding.assessment_round, 1)
        self.assertIn("TAF_window_coverage", finding.missing_inputs)
        self.assertIn("observed wind_speed_kt: 5 kt", finding.summary)
        self.assertEqual(finding.finding_id, reply.assessment_id)
        self.assertEqual(json.loads(storage_json(finding))["status"], "insufficient_data")
        self.adapter.save_weather.assert_awaited_once()

    async def test_snapshot_cannot_include_weather_fetched_after_cutoff(self):
        self.record["fetched_at_utc"] = (NOW + timedelta(seconds=1)).isoformat()
        with self.assertRaises(ValueError):
            await self.storage.load_snapshot("snap-1")

    async def test_snapshot_rejects_content_with_different_source(self):
        self.record["content_json"] = json.dumps(["https://wrong.example/TEST", "{}"])
        with self.assertRaises(ValueError):
            await self.storage.load_snapshot("snap-1")

    async def test_missing_weather_produces_a_saved_insufficient_finding(self):
        self.adapter.load_snapshot.return_value["weather"] = []
        snapshot = await self.storage.load_snapshot("snap-1")
        service = WeatherService(
            WeatherDependencies(self.storage.load_snapshot, self.storage.persist_assessment),
            clock=lambda: NOW,
        )
        reply = await service.handle(request(snapshot, message_id="case-1:weather-request:1"))
        self.assertEqual(reply.status, "insufficient_data")
        finding = self.adapter.save_weather.call_args.args[0]
        self.assertIn("fresh_METAR", finding.missing_inputs)
        self.assertEqual(finding.quality, "unavailable")

    async def test_persistence_rejects_an_unbound_assessment(self):
        snapshot = await self.storage.load_snapshot("snap-1")
        service = WeatherService(
            WeatherDependencies(self.storage.load_snapshot, AsyncMock()), clock=lambda: NOW
        )
        await service.handle(request(snapshot))
        result = service._requests["request-1"].result
        with self.assertRaises(ValueError):
            await self.storage.persist_assessment(replace(result, summary="TEST"))

    async def test_full_metar_and_taf_survive_storage_and_support_clear_weather(self):
        source = snapshot()
        normalized = normalized_metar(source.metars[0], source.tafs)
        retained = json.loads(storage_json(normalized))
        self.record.update(
            source_ref=normalized.source_ref,
            content_json=json.dumps(
                [
                    normalized.source_ref,
                    json.dumps({}),
                    {
                        "metar_evidence": retained["metar_evidence"],
                        "taf_evidence": retained["taf_evidence"],
                    },
                ]
            ),
        )
        self.adapter.load_snapshot.return_value["evidence_ids"].append("snap-1:weather:awc:taf:0")
        loaded = await self.storage.load_snapshot("snap-1")
        self.assertEqual(loaded.metars[0].conditions, source.metars[0].conditions)
        self.assertEqual(loaded.tafs[0].groups, source.tafs[0].groups)
        service = WeatherService(
            WeatherDependencies(self.storage.load_snapshot, self.storage.persist_assessment),
            clock=lambda: NOW,
        )
        reply = await service.handle(request(loaded, message_id="case-1:weather-request:1"))
        self.assertEqual(reply.status, WeatherStatus.NO_MATERIAL_IMPACT)
        self.assertEqual(self.adapter.save_weather.call_args.args[0].missing_inputs, ())

    async def test_full_metar_without_forecast_retains_specific_gap(self):
        source = snapshot()
        record = normalized_metar(source.metars[0])
        retained = json.loads(storage_json(record))
        self.record.update(
            source_ref=record.source_ref,
            content_json=json.dumps(
                [
                    record.source_ref,
                    "{}",
                    {"metar_evidence": retained["metar_evidence"], "taf_evidence": []},
                ]
            ),
        )
        loaded = await self.storage.load_snapshot("snap-1")
        service = WeatherService(
            WeatherDependencies(self.storage.load_snapshot, self.storage.persist_assessment),
            clock=lambda: NOW,
        )
        reply = await service.handle(request(loaded, message_id="case-1:weather-request:1"))
        self.assertEqual(reply.status, WeatherStatus.INSUFFICIENT_DATA)
        self.assertEqual(
            self.adapter.save_weather.call_args.args[0].missing_inputs, ("TAF_window_coverage",)
        )
