"""Material changes, rate limits, retry identity and quality transitions."""

import asyncio
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from agents.weather.tests.helpers import NOW, metar_payload, snapshot, taf_payload
from agents.weather.triggers import ChangeDetector, TriggerDependencies
from shared.contracts import AgentName


class TriggerTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.now = NOW
        self.snap = snapshot()
        self.obtain = AsyncMock(side_effect=lambda: self.snap)
        self.open = AsyncMock()
        self.detector = ChangeDetector(
            TriggerDependencies(self.obtain, self.open), clock=lambda: self.now
        )

    async def test_quiet_unchanged_fetches_onset_and_clearance(self) -> None:
        self.assertFalse(await self.detector.poll())
        self.now += timedelta(seconds=60)
        self.snap = snapshot(now=self.now, snapshot_id="snap-refresh")
        self.assertFalse(await self.detector.poll())
        self.snap = snapshot(now=self.now, metar={**metar_payload(), "wspd": 25})
        self.assertTrue(await self.detector.poll())
        envelope = self.open.call_args.args[0]
        self.assertEqual(envelope.sender_agent, AgentName.WEATHER)
        self.assertEqual(envelope.evidence_ids, self.snap.evidence_ids)
        self.assertFalse(await self.detector.poll())
        self.snap = snapshot(now=self.now)
        self.assertFalse(await self.detector.poll())
        self.now += timedelta(seconds=60)
        self.assertTrue(await self.detector.poll())
        self.assertEqual(self.open.await_count, 2)

    async def test_wind_change_wraparound_and_quality_transition(self) -> None:
        self.snap = snapshot(metar={**metar_payload(), "wdir": 350})
        await self.detector.poll()
        self.snap = snapshot(metar={**metar_payload(), "wdir": 10})
        self.assertFalse(await self.detector.poll())
        self.snap = snapshot(metar={**metar_payload(), "wdir": 60})
        self.assertTrue(await self.detector.poll())
        self.now += timedelta(seconds=60)
        self.snap = snapshot(metar=False, taf=False, now=self.now)
        self.assertTrue(await self.detector.poll())

    async def test_failed_case_open_retries_identical_envelope(self) -> None:
        self.snap = snapshot(metar={**metar_payload(), "wspd": 25})
        self.open.side_effect = [RuntimeError("storage unavailable"), None]
        with self.assertRaises(RuntimeError):
            await self.detector.poll()
        first = self.open.call_args.args[0]
        self.now += timedelta(seconds=1)
        self.assertTrue(await self.detector.poll())
        self.assertEqual(first, self.open.call_args.args[0])
        self.obtain.assert_awaited_once()

    async def test_relevant_amendment_and_no_overlapping_poll(self) -> None:
        await self.detector.poll()
        taf = taf_payload()
        taf["rawTAF"] = "TEST TAF AMD KDTW"
        self.snap = snapshot(taf=taf)
        self.assertTrue(await self.detector.poll())
        gate = asyncio.Event()

        async def obtain():  # type: ignore[no-untyped-def]
            await gate.wait()
            return self.snap

        self.obtain.side_effect = obtain
        first = asyncio.create_task(self.detector.poll())
        await asyncio.sleep(0.01)
        self.assertFalse(await self.detector.poll())
        gate.set()
        self.assertFalse(await first)

    async def test_gust_change_and_forecast_timing_change(self) -> None:
        self.snap = snapshot(metar={**metar_payload(), "wgst": 30})
        self.assertTrue(await self.detector.poll())

        self.now += timedelta(seconds=60)
        self.snap = snapshot(now=self.now, metar={**metar_payload(), "wgst": 40})
        self.assertTrue(await self.detector.poll())
        self.now += timedelta(seconds=60)
        taf = taf_payload()
        taf["fcsts"].append(
            {
                "timeFrom": (self.now + timedelta(minutes=5)).timestamp(),
                "timeTo": (self.now + timedelta(minutes=10)).timestamp(),
                "fcstChange": "TEMPO",
                "wxString": "TSRA",
            }
        )
        self.snap = snapshot(now=self.now, taf=taf)
        self.assertTrue(await self.detector.poll())
        self.now += timedelta(seconds=60)
        taf["fcsts"][-1]["timeFrom"] += 60
        self.snap = snapshot(now=self.now, taf=taf)
        self.assertTrue(await self.detector.poll())

    async def test_snapshot_created_during_read_uses_post_read_clock(self) -> None:
        async def obtain():  # type: ignore[no-untyped-def]
            self.now += timedelta(seconds=1)
            return snapshot(now=self.now, metar={**metar_payload(), "wspd": 25})

        self.obtain.side_effect = obtain
        self.assertTrue(await self.detector.poll())
        self.assertEqual(self.open.call_args.args[0].created_at_utc, self.now)
