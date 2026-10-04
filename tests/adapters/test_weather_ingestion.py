"""Live poller's unknown input and lost-acknowledgement behavior, using TEST evidence."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock

from agents.weather.tests.helpers import NOW, snapshot
from data.awc import AwcError, FetchResult
from data.weather_ingestion import WeatherIngestion, normalized_metar
from shared.contracts import VisibilityBound
from shared.spacetimedb_adapter import SpacetimeError, payload_json


class NormalizationTests(TestCase):
    def test_forecast_submillisecond_times_are_normalized_before_the_wire_commit(self):
        source = snapshot()
        metar = replace(source.metars[0], fetched_at_utc=NOW.replace(microsecond=999999))
        taf = replace(source.tafs[0], fetched_at_utc=NOW.replace(microsecond=123456))
        record = normalized_metar(metar, (taf,))
        self.assertEqual(record.taf_evidence[0].fetched_at_utc.microsecond, 123000)
        self.assertIn('"fetched_at_utc":"2026-10-04T05:00:00.123Z"', payload_json(record))

    def test_fetch_precision_is_normalized_and_visibility_bounds_remain_unknown(self):
        metar = snapshot().metars[0]
        metar = replace(
            metar,
            fetched_at_utc=NOW.replace(microsecond=123456),
            conditions=replace(metar.conditions, visibility_bound=VisibilityBound.GREATER_THAN),
        )
        value = normalized_metar(metar)
        self.assertEqual(value.fetched_at_utc.microsecond, 123000)
        self.assertIsNone(value.visibility_m)

    def test_retained_observation_must_agree_with_its_scalar_and_timestamp_fields(self):
        source = snapshot()
        record = normalized_metar(source.metars[0], source.tafs)
        for change in ({"wind_speed_kt": 999}, {"source_ref": "https://wrong.example"}):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(record, **change)
        with self.assertRaises(ValueError):
            replace(
                record,
                taf_evidence=(replace(source.tafs[0], fetched_at_utc=NOW.replace(second=1)),),
            )


class PollTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client, self.adapter = AsyncMock(), AsyncMock()
        self.client.fetch.return_value = FetchResult("metar", NOW, snapshot().metars)
        self.poller = WeatherIngestion(self.client, self.adapter)
        self.poller.taf_fetched = datetime.now(UTC)

    async def test_failed_commit_retries_identical_record_before_fetching_again(self):
        self.adapter.ingest_weather_record.side_effect = [SpacetimeError("TEST lost ack"), None]
        await self.poller.poll()
        saved = self.poller.pending
        await self.poller.poll()
        self.client.fetch.assert_awaited_once_with("metar")
        self.assertEqual(self.adapter.ingest_weather_record.await_args_list[1].args[0], saved)
        self.assertIsNone(self.poller.pending)

    async def test_empty_or_failed_fetch_updates_health_without_inventing_weather(self):
        for result in (FetchResult("metar", NOW, ()), AwcError("TEST unavailable")):
            self.client.fetch.side_effect = result if isinstance(result, Exception) else None
            self.client.fetch.return_value = result
            await self.poller.poll()
        self.adapter.ingest_weather_record.assert_not_awaited()
        self.assertEqual(self.adapter.report_source_failure.await_count, 2)

    async def test_forecast_failure_still_commits_complete_current_observation(self):
        self.poller.taf_fetched = None
        self.client.fetch.side_effect = [
            AwcError("TEST forecast down"),
            FetchResult("metar", NOW, snapshot().metars),
        ]
        await self.poller.poll()
        record = self.adapter.ingest_weather_record.call_args.args[0]
        self.assertEqual(record.taf_evidence, ())
        self.assertEqual(record.metar_evidence.conditions, snapshot().metars[0].conditions)

    async def test_forecast_is_retained_and_pending_commit_does_not_refetch(self):
        self.poller.taf_fetched = None
        self.client.fetch.side_effect = [
            FetchResult("taf", NOW, snapshot().tafs),
            FetchResult("metar", NOW, snapshot().metars),
        ]
        self.adapter.ingest_weather_record.side_effect = [SpacetimeError("TEST lost ack"), None]
        await self.poller.poll()
        record = self.poller.pending
        await self.poller.poll()
        self.assertEqual(self.client.fetch.await_count, 2)
        self.assertEqual(self.adapter.ingest_weather_record.await_args_list[-1].args[0], record)
        self.assertEqual(record.taf_evidence, snapshot().tafs)
