"""Durable submission ordering, injected failures, concurrency and frozen inputs."""

import asyncio
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock, MagicMock

from agents.congestion.feeds import stored_feeds
from agents.congestion.metrics import nearby_traffic
from agents.congestion.persistence import PersistentCongestionService
from agents.congestion.service import CongestionService
from agents.congestion.tests.test_service import validation_request, weather_reply
from data.adsb import parse_point_response
from shared.persistence import storage_json
from shared.spacetimedb_adapter import SpacetimeAdapter, SpacetimeError
from tests.fixtures.spacetimedb.congestion import snapshot_fixture

NOW = datetime(2026, 9, 21, 4, 53, 21, tzinfo=UTC)


class PersistenceTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.snapshot = snapshot_fixture(NOW)
        self.adapter = MagicMock(spec=SpacetimeAdapter)
        self.adapter.call = AsyncMock()
        self.adapter.create_snapshot = AsyncMock(return_value=self.snapshot)
        self.adapter.save_candidate = AsyncMock()
        self.adapter.pending_deliveries = AsyncMock(return_value=())
        self.durable = PersistentCongestionService(self.adapter)
        self.opening = await self.durable.cycle(NOW)
        self.request = validation_request(self.opening)
        weather = await self.durable.assessment(self.request, NOW + timedelta(seconds=2))
        await self.durable.weather(
            weather_reply(weather.envelope.message_id, self.opening, NOW + timedelta(seconds=3)),
            "no_material_impact",
            "TEST-weather",
            NOW + timedelta(seconds=3),
        )
        self.send = AsyncMock()

    async def test_failed_save_sends_nothing_and_retries_identical_candidate(self) -> None:
        self.adapter.save_candidate.side_effect = [SpacetimeError("TEST unavailable"), None]
        with self.assertRaises(SpacetimeError):
            await self.durable.advance(NOW + timedelta(seconds=4), self.send)
        self.send.assert_not_awaited()
        first = self.adapter.save_candidate.call_args.args
        await self.durable.advance(NOW + timedelta(seconds=5), self.send)
        self.assertEqual(first, self.adapter.save_candidate.call_args.args)
        self.assertEqual(self.adapter.save_candidate.await_count, 2)

    async def test_concurrent_cycles_and_advances_submit_one_round(self) -> None:
        await asyncio.gather(*(self.durable.cycle(NOW) for _ in range(8)))
        self.adapter.create_snapshot.assert_awaited_once()
        await asyncio.gather(
            *(self.durable.advance(NOW + timedelta(seconds=4), self.send) for _ in range(8))
        )
        self.adapter.save_candidate.assert_awaited_once()
        candidate, ready = self.adapter.save_candidate.call_args.args
        self.assertEqual(candidate.envelope.snapshot_id, self.opening.snapshot_id)
        self.assertEqual(ready.assessment_round, 1)

    async def test_restart_reloads_outbox_and_closes_volatile_assessments(self) -> None:
        await self.durable.advance(NOW + timedelta(seconds=4), self.send)
        _, ready = self.adapter.save_candidate.call_args.args
        self.adapter.pending_deliveries.return_value = (ready,)
        restarted = PersistentCongestionService(self.adapter)
        await restarted.advance(NOW + timedelta(seconds=5), self.send)
        self.send.assert_awaited_once_with(ready)
        self.assertEqual(restarted.service.open_cases, ())
        self.assertEqual(self.adapter.call.call_args.args, ("close_unfinished_cases",))

    async def test_delivery_failure_retries_until_receipt_or_expiry(self) -> None:
        await self.durable.advance(NOW + timedelta(seconds=4), self.send)
        _, ready = self.adapter.save_candidate.call_args.args
        self.adapter.pending_deliveries.return_value = (ready,)
        self.send.side_effect = OSError("TEST failure")
        await self.durable.advance(NOW + timedelta(seconds=5), self.send)
        self.send.side_effect = None
        await self.durable.advance(NOW + timedelta(seconds=6), self.send)
        self.assertEqual(self.send.await_count, 2)
        await self.durable.advance(NOW + timedelta(seconds=100), self.send)
        self.assertEqual(self.send.await_count, 2)
        self.adapter.pending_deliveries.return_value = ()
        await self.durable.advance(NOW + timedelta(seconds=101), self.send)
        self.assertEqual(self.send.await_count, 2)

    async def test_both_weather_rounds_keep_original_window_and_evidence(self) -> None:
        await self.durable.advance(NOW + timedelta(seconds=4), self.send)
        candidate, first = self.adapter.save_candidate.call_args.args
        request = validation_request(self.opening, 2, reply_to=first.candidate_id)
        second_weather = await self.durable.assessment(request, NOW + timedelta(seconds=5))
        self.assertEqual(second_weather.window_start_utc, candidate.window_start_utc)
        self.assertEqual(second_weather.window_end_utc, candidate.window_end_utc)
        await self.durable.weather(
            weather_reply(
                second_weather.envelope.message_id, self.opening, NOW + timedelta(seconds=6), 2
            ),
            "impact",
            "TEST-weather-two",
            NOW + timedelta(seconds=6),
        )
        await self.durable.advance(NOW + timedelta(seconds=7), self.send)
        second, _ = self.adapter.save_candidate.call_args.args
        self.assertEqual(
            (second.window_start_utc, second.window_end_utc),
            (candidate.window_start_utc, candidate.window_end_utc),
        )
        self.assertEqual(second.envelope.snapshot_id, candidate.envelope.snapshot_id)
        self.assertNotEqual(second.envelope.message_id, candidate.envelope.message_id)

    async def test_parser_and_stored_snapshot_compute_equivalent_candidate(self) -> None:
        stored = stored_feeds(self.snapshot)
        direct = replace(
            stored,
            adsb=parse_point_response(
                {
                    "now": NOW.timestamp(),
                    "total": 1,
                    "ac": [
                        {
                            "hex": "a00001",
                            "lat": 42.22,
                            "lon": -83.35,
                            "alt_baro": 3000,
                            "seen_pos": 0,
                            "gs": 160,
                            "track": 180,
                            "flight": "TEST001",
                        }
                    ],
                },
                NOW,
            ),
        )
        self.assertEqual(nearby_traffic(stored.adsb, NOW), nearby_traffic(direct.adsb, NOW))
        built = []
        for feeds in (stored, direct):
            svc = CongestionService()
            opening = svc.start_cycle(feeds, NOW)
            wx = svc.on_assessment_request(validation_request(opening), NOW + timedelta(seconds=2))
            svc.on_weather_reply(
                weather_reply(wx.envelope.message_id, opening, NOW + timedelta(seconds=3)),
                "no_material_impact",
                "TEST-weather",
                NOW + timedelta(seconds=3),
            )
            (ready,) = svc.advance(NOW + timedelta(seconds=4)).ready
            built.append(svc.candidates[ready.candidate_id])
        self.assertEqual(built[0], built[1])
        self.assertIsNone(built[0].projection_id)
        self.assertFalse(any("demand" in metric.name for metric in built[0].traffic_metrics))

    async def test_missing_stale_empty_and_failed_batches_remain_distinct(self) -> None:
        self.assertIsNone(
            nearby_traffic(stored_feeds(snapshot_fixture(NOW, adsb=False)).adsb, NOW).total
        )
        self.assertIsNone(
            nearby_traffic(stored_feeds(self.snapshot).adsb, NOW + timedelta(seconds=61)).total
        )
        raw = json.loads(storage_json(self.snapshot))
        raw["batches"][0]["payload"]["snapshot"]["observations"] = []
        raw["batches"][0]["payload"]["snapshot"]["reported_total"] = 0
        self.assertEqual(nearby_traffic(stored_feeds(raw).adsb, NOW).total, 0)
        raw["health"][0]["status"] = "failed"
        self.assertIsNone(nearby_traffic(stored_feeds(raw).adsb, NOW).total)
