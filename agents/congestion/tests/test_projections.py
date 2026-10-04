"""Stored-snapshot projections: reducer-rule demand, payload shape, service and persistence."""

import json
import math
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock

from agents.congestion.feeds import FeedSnapshot, stored_feeds
from agents.congestion.persistence import PersistentCongestionService
from agents.congestion.projections import (
    BACKLOG_NOTE,
    ProjectionSettings,
    prepare_projection,
)
from agents.congestion.service import CongestionService
from agents.congestion.tests.test_service import validation_request, weather_reply
from projection.aggregate_flow import NO_RESPONSE, compare
from projection.assumptions import CapacityBasis
from shared.contracts import EvidenceQuality, MetricBasis
from shared.persistence import storage_json
from shared.spacetimedb_adapter import SpacetimeAdapter, SpacetimeError
from tests.fixtures.spacetimedb.congestion import snapshot_fixture

NOW = datetime(2026, 9, 21, 4, 50, tzinfo=UTC)
SETTINGS = ProjectionSettings(capacity_per_hour=30, backlog_upstream=6, backlog_runway_queue=4)
FLIGHT_BATCH = "TEST-flightaware-batch"
REDUCER_KEYS = {
    "projection_id",
    "snapshot_id",
    "model_version",
    "evidence_ids",
    "input",
    "assumptions",
    "response",
    "result",
}


def flight(n: int, minutes: float, **changes: object) -> dict[str, Any]:
    record: dict[str, Any] = {
        "flight_id": f"TEST-flight-{n}",
        "direction": "departure",
        "time_utc": NOW + timedelta(minutes=minutes),
        "time_kind": "estimated_runway",
        "association": "confirmed_record",
        "cancelled": False,
    }
    return record | changes


def mixed_records() -> list[dict[str, Any]]:
    """Counted and excluded cases at the reducer's bucket edges."""
    counted = [flight(i, 0) for i in range(7)]  # 7 at the window start: remainder 2
    counted += [flight(10, 5), flight(11, 9.99), flight(12, 14.99)]
    excluded = [
        flight(20, 3, cancelled=True),
        flight(21, 3, association="inferred"),
        flight(22, 3, time_kind="scheduled_gate"),
        flight(23, 3, direction="arrival"),
        flight(24, -0.01),
        flight(25, 15),
        flight(26, 0, time_utc=None, time_kind=None),
    ]
    return counted + excluded


def stored_snapshot(
    records: list[dict[str, Any]] | None = None, *, fetched: datetime = NOW
) -> dict[str, Any]:
    """The partner's TEST snapshot plus one stored FlightAware batch (TEST data)."""
    value = snapshot_fixture(NOW)
    batch = {
        "batch_id": FLIGHT_BATCH,
        "schema_version": 1,
        "data_mode": "live",
        "provider": "flightaware",
        "source_ref": "https://example.test/TEST-departures",
        "configuration": value["configuration"],
        "fetched_at_utc": fetched,
        "records": mixed_records() if records is None else records,
    }
    value["batches"].append({"batch_id": FLIGHT_BATCH, "payload": batch})
    value["health"].append({"provider": "flightaware", "status": "ok"})
    value["evidence_ids"].append(FLIGHT_BATCH)
    return json.loads(storage_json(value))  # type: ignore[no-any-return]


def reducer_demand(snapshot: dict[str, Any], horizon: int) -> list[int]:
    """Independent copy of evidence_rules.ts projectionInputs' demand derivation."""
    start = datetime.fromisoformat(snapshot["window_start_utc"].replace("Z", "+00:00"))
    buckets = [0] * (horizon // 5)
    batch = next(b for b in snapshot["batches"] if b["payload"]["provider"] == "flightaware")
    for r in batch["payload"]["records"]:
        if (
            r["direction"] != "departure"
            or r["association"] != "confirmed_record"
            or r["cancelled"] is True
            or r["time_utc"] is None
            or r["time_kind"] == "scheduled_gate"
        ):
            continue
        at = datetime.fromisoformat(r["time_utc"].replace("Z", "+00:00"))
        bucket = math.floor((at - start).total_seconds() * 1000 / 300000)
        if 0 <= bucket < len(buckets):
            buckets[bucket] += 1
    return [n // 5 + (1 if i < n % 5 else 0) for n in buckets for i in range(5)]


class PrepareProjectionTests(TestCase):
    def test_demand_matches_the_reducer_rule(self) -> None:
        snapshot = stored_snapshot()
        prepared = prepare_projection(stored_feeds(snapshot), SETTINGS)
        assert prepared is not None
        self.assertEqual(
            list(prepared.snapshot.demand_per_minute or ()), reducer_demand(snapshot, 15)
        )
        self.assertEqual(
            prepared.snapshot.demand_per_minute, (2, 2, 1, 1, 1, 1, 1, 0, 0, 0, 1, 0, 0, 0, 0)
        )

    def test_inputs_are_the_snapshot_and_operator_settings(self) -> None:
        prepared = prepare_projection(stored_feeds(stored_snapshot()), SETTINGS)
        assert prepared is not None
        snapshot = prepared.snapshot
        self.assertEqual((snapshot.snapshot_id, snapshot.as_of_utc), ("TEST-snapshot", NOW))
        self.assertEqual((snapshot.initial_upstream_waiting, snapshot.initial_runway_queue), (6, 4))
        self.assertIn(BACKLOG_NOTE, snapshot.demand_basis)
        capacity = prepared.assumptions.capacity
        assert capacity is not None
        self.assertEqual(capacity.effective_capacity_per_hour, 30)
        self.assertIs(capacity.basis, CapacityBasis.OPERATOR_CONFIGURED)
        self.assertEqual(prepared.projection_id, "proj-TEST-snapshot")
        self.assertEqual(prepared.evidence_id, FLIGHT_BATCH)

    def test_unsupported_inputs_give_no_projection(self) -> None:
        feeds = stored_feeds(stored_snapshot())
        cases: dict[str, tuple[FeedSnapshot, ProjectionSettings | None]] = {
            "not configured": (feeds, None),
            "no demand": (stored_feeds(snapshot_fixture(NOW)), SETTINGS),
            "stale demand": (
                stored_feeds(stored_snapshot(fetched=NOW - timedelta(minutes=5, seconds=1))),
                SETTINGS,
            ),
            "no demand evidence": (replace(feeds, demand_evidence_id=None), SETTINGS),
            "unsupported horizon": (
                replace(feeds, window_end_utc=NOW + timedelta(minutes=20)),
                SETTINGS,
            ),
            "partial minute": (
                replace(feeds, window_end_utc=NOW + timedelta(minutes=15, seconds=30)),
                SETTINGS,
            ),
        }
        for name, (case_feeds, settings) in cases.items():
            with self.subTest(name):
                self.assertIsNone(prepare_projection(case_feeds, settings))

    def test_empty_but_fresh_demand_is_computed_not_skipped(self) -> None:
        prepared = prepare_projection(stored_feeds(stored_snapshot([])), SETTINGS)
        assert prepared is not None
        self.assertEqual(prepared.snapshot.demand_per_minute, (0,) * 15)

    def test_settings_are_bounded_counts(self) -> None:
        for values in ((5, 0, 0), (121, 0, 0), (30, -1, 0), (30, 0, True)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                ProjectionSettings(*values)  # type: ignore[arg-type]

    def test_payload_has_reducer_fields_and_recomputes_after_storage(self) -> None:
        prepared = prepare_projection(stored_feeds(stored_snapshot()), SETTINGS)
        assert prepared is not None
        payload = prepared.payload()
        self.assertEqual(set(payload), REDUCER_KEYS)
        self.assertEqual(payload["evidence_ids"], [FLIGHT_BATCH])
        stored = json.loads(storage_json(payload))
        self.assertEqual(stored["input"]["as_of_utc"], "2026-09-21T04:50:00.000Z")
        self.assertEqual(len(stored["input"]["demand_per_minute"]), 15)
        self.assertEqual(
            stored["result"]["baseline"]["response"],
            {"release_cap_per_hour": None, "window_start_minute": 0, "window_end_minute": 0},
        )
        self.assertEqual(
            prepared.comparison.baseline and prepared.comparison.baseline.response, NO_RESPONSE
        )
        recomputed = compare(prepared.snapshot, prepared.assumptions, prepared.response)
        self.assertEqual(json.loads(storage_json(recomputed)), stored["result"])

    def test_same_snapshot_gives_the_same_projection(self) -> None:
        first = prepare_projection(stored_feeds(stored_snapshot()), SETTINGS)
        second = prepare_projection(stored_feeds(stored_snapshot()), SETTINGS)
        assert first is not None and second is not None
        self.assertEqual(storage_json(first.payload()), storage_json(second.payload()))


class ServiceProjectionTests(TestCase):
    def candidate(self, service: CongestionService):  # type: ignore[no-untyped-def]
        opening = service.start_cycle(stored_feeds(stored_snapshot()), NOW)
        request = validation_request(opening)
        weather = service.on_assessment_request(request, request.envelope.created_at_utc)
        when = request.envelope.created_at_utc + timedelta(seconds=1)
        service.on_weather_reply(
            weather_reply(weather.envelope.message_id, opening, when),
            "no_material_impact",
            "TEST-weather",
            when,
        )
        (ready,) = service.advance(when + timedelta(seconds=1)).ready
        return opening, ready, service.candidates[ready.candidate_id]

    def test_configured_service_cites_its_projection(self) -> None:
        service = CongestionService(projection=SETTINGS)
        opening, ready, candidate = self.candidate(service)
        self.assertEqual(candidate.projection_id, "proj-TEST-snapshot")
        self.assertEqual(ready.projection_id, candidate.projection_id)
        self.assertIn(candidate.projection_id, service.projections)
        self.assertIn(candidate.projection_id, candidate.envelope.evidence_ids)
        self.assertNotIn(candidate.projection_id, opening.evidence_ids)  # derived, not input
        projected = {m.name for m in candidate.traffic_metrics if m.basis is MetricBasis.PROJECTED}
        self.assertEqual(len(projected), 3)
        self.assertIs(candidate.quality, EvidenceQuality.LIMITED)
        self.assertIn(BACKLOG_NOTE, candidate.assumptions)

    def test_unconfigured_service_is_unchanged(self) -> None:
        service = CongestionService()
        _, ready, candidate = self.candidate(service)
        self.assertIsNone(candidate.projection_id)
        self.assertIsNone(ready.projection_id)
        self.assertEqual(service.projections, {})
        self.assertNotIn(BACKLOG_NOTE, candidate.assumptions)


class PersistedProjectionTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.adapter = MagicMock(spec=SpacetimeAdapter)
        self.adapter.call = AsyncMock()
        self.adapter.create_snapshot = AsyncMock(return_value=stored_snapshot())
        self.adapter.store_projection = AsyncMock()
        self.adapter.save_candidate = AsyncMock()
        self.adapter.pending_deliveries = AsyncMock(return_value=())
        self.durable = PersistentCongestionService(
            self.adapter, service=CongestionService(projection=SETTINGS)
        )
        opening = await self.durable.cycle(NOW)
        request = validation_request(opening)
        weather = await self.durable.assessment(request, request.envelope.created_at_utc)
        self.when = request.envelope.created_at_utc + timedelta(seconds=1)
        await self.durable.weather(
            weather_reply(weather.envelope.message_id, opening, self.when),
            "no_material_impact",
            "TEST-weather",
            self.when,
        )

    async def test_projection_is_stored_before_the_candidate_that_cites_it(self) -> None:
        await self.durable.advance(self.when + timedelta(seconds=1), AsyncMock())
        names = [
            c[0] for c in self.adapter.mock_calls if c[0] in ("store_projection", "save_candidate")
        ]
        self.assertEqual(names, ["store_projection", "save_candidate"])
        (payload,) = self.adapter.store_projection.call_args.args
        candidate, _ = self.adapter.save_candidate.call_args.args
        self.assertEqual(payload["projection_id"], candidate.projection_id)

    async def test_failed_candidate_save_retries_the_identical_projection(self) -> None:
        self.adapter.save_candidate.side_effect = [SpacetimeError("TEST unavailable"), None]
        with self.assertRaises(SpacetimeError):
            await self.durable.advance(self.when + timedelta(seconds=1), AsyncMock())
        first = storage_json(self.adapter.store_projection.call_args.args[0])
        await self.durable.advance(self.when + timedelta(seconds=2), AsyncMock())
        self.assertEqual(self.adapter.store_projection.await_count, 2)
        self.assertEqual(storage_json(self.adapter.store_projection.call_args.args[0]), first)
        self.adapter.save_candidate.assert_awaited()

    async def test_failed_projection_store_saves_no_candidate(self) -> None:
        self.adapter.store_projection.side_effect = SpacetimeError("TEST unavailable")
        with self.assertRaises(SpacetimeError):
            await self.durable.advance(self.when + timedelta(seconds=1), AsyncMock())
        self.adapter.save_candidate.assert_not_awaited()
