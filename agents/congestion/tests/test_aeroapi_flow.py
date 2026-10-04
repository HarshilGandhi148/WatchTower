"""AeroAPI demand in the congestion agent: backlog, guarded fetch, service projection."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from agents.congestion.agent import AgentConfig
from agents.congestion.feeds import AeroApiFetcher, FeedSnapshot
from agents.congestion.metrics import demand_windows, departure_backlog
from agents.congestion.service import CongestionService
from data.flightaware import AeroApiConfig, Endpoint, parse_flights
from projection.assumptions import CapacityBasis, CapacityScenario, ProjectionAssumptions
from shared.contracts import AgentName, MessageEnvelope, MetricBasis
from shared.orchestration import AssessmentRequest

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests" / "fixtures" / "flightaware"
NOW = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)
AERO_ENV = {
    "ENABLE_FLIGHTAWARE": "true",
    "FLIGHTAWARE_API_KEY": "test-key",
    "FLIGHTAWARE_BASE_URL": "https://aeroapi.flightaware.com/aeroapi",
    "FLIGHTAWARE_BUDGET_USD": "1.00",
    "FLIGHTAWARE_COST_PER_RESULT_SET_USD": "0.10",
}


def departures_json() -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / "scheduled_departures.json").read_text(encoding="utf-8"))


def arrivals_json(truncated: bool = False) -> dict:  # type: ignore[type-arg]
    data = json.loads((FIXTURES / "scheduled_arrivals.json").read_text(encoding="utf-8"))
    if not truncated:
        data["links"] = None
    return data


def records() -> tuple:  # type: ignore[type-arg]
    dep = parse_flights(departures_json(), Endpoint.SCHEDULED_DEPARTURES, NOW).records
    arr = parse_flights(arrivals_json(), Endpoint.SCHEDULED_ARRIVALS, NOW).records
    return dep + arr


def aero_config(**changes: str) -> AeroApiConfig:
    config = AeroApiConfig.from_env({**AERO_ENV, **changes})
    assert config is not None
    return config


class BacklogTests(TestCase):
    def test_taxiing_is_runway_queue_and_held_is_upstream(self) -> None:
        backlog = departure_backlog(records(), NOW, NOW)
        self.assertEqual((backlog.upstream_waiting, backlog.runway_queue), (1, 1))
        self.assertEqual(backlog.flight_ids, frozenset({"TEST-DEP-TAXIING", "TEST-DEP-HELD"}))
        self.assertEqual(backlog.unclassified, 1)  # the record with no usable gate times
        self.assertTrue(any("readiness is not observed" in a for a in backlog.assumptions))

    def test_missing_or_stale_records_are_unknown_not_zero(self) -> None:
        for recs, fetched in (
            (None, NOW),
            (records(), None),
            (records(), NOW - timedelta(minutes=10)),
        ):
            with self.subTest(fetched=fetched):
                backlog = departure_backlog(recs, fetched, NOW)
                self.assertIsNone(backlog.upstream_waiting)
                self.assertIsNone(backlog.runway_queue)
                self.assertTrue(backlog.unavailable_reason)

    def test_backlog_flights_are_not_counted_again_as_demand(self) -> None:
        backlog = departure_backlog(records(), NOW, NOW)
        windows = demand_windows(records(), NOW, NOW, exclude_flight_ids=backlog.flight_ids)
        assert windows.windows
        self.assertEqual([w.departures_confirmed for w in windows.windows], [0, 0, 1])
        self.assertEqual([w.arrivals_confirmed for w in windows.windows], [0, 1, 0])
        # gate-only (no taxi assumption), cancelled, untimed and diverted are unplaced
        self.assertEqual(windows.unplaced_records, 4)
        joined = " ".join(windows.assumptions)
        self.assertIn("2 departures already in the backlog", joined)
        self.assertIn("scheduled runway times", joined)


class FetcherTests(TestCase):
    def fake_get(self, *, fail: bool = False, truncated: bool = False):  # type: ignore[no-untyped-def]
        calls: list[str] = []

        def get(url: str, headers: dict) -> bytes:  # type: ignore[type-arg]
            calls.append(url)
            self.assertEqual(headers["x-apikey"], AERO_ENV["FLIGHTAWARE_API_KEY"])
            if fail:
                raise OSError("connection reset")
            if "scheduled_departures" in url:
                return json.dumps(departures_json()).encode()
            return json.dumps(arrivals_json(truncated)).encode()

        get.calls = calls  # type: ignore[attr-defined]
        return get

    def test_fetch_combines_both_endpoints_and_spends_from_the_budget(self) -> None:
        get = self.fake_get()
        fetcher = AeroApiFetcher(aero_config(), "UA (TEST)", get=get)
        recs, fetched_at, error = fetcher.fetch(NOW)
        assert recs is not None
        self.assertIsNone(error)
        self.assertEqual(fetched_at, NOW)
        self.assertEqual(len(get.calls), 2)
        self.assertEqual(fetcher.budget.spent_usd, Decimal("0.20"))
        departures_url, arrivals_url = get.calls
        self.assertIn("start=2026-10-04T14%3A00%3A00Z", departures_url)  # 2 h lookback
        self.assertIn("start=2026-10-04T16%3A00%3A00Z", arrivals_url)  # arrivals from now
        self.assertIn("end=2026-10-04T17%3A00%3A00Z", arrivals_url)
        self.assertTrue(any(r.flight_id == "TEST-ARR-ESTIMATED" for r in recs))

    def test_cache_prevents_paid_calls_inside_the_refresh_period(self) -> None:
        get = self.fake_get()
        fetcher = AeroApiFetcher(aero_config(), "UA (TEST)", get=get)
        fetcher.fetch(NOW)
        _, fetched_at, _ = fetcher.fetch(NOW + timedelta(minutes=4))
        self.assertEqual((len(get.calls), fetched_at), (2, NOW))
        fetcher.fetch(NOW + timedelta(minutes=5))
        self.assertEqual(len(get.calls), 4)

    def test_budget_exhaustion_makes_demand_unavailable(self) -> None:
        get = self.fake_get()
        fetcher = AeroApiFetcher(aero_config(FLIGHTAWARE_BUDGET_USD="0.15"), "UA", get=get)
        recs, _, error = fetcher.fetch(NOW)
        self.assertIsNone(recs)
        self.assertIn("budget", error or "")
        self.assertEqual(len(get.calls), 1)  # the refused request was never sent

    def test_failures_and_truncation_are_unavailable_and_never_leak_the_key(self) -> None:
        for get, expected in (
            (self.fake_get(fail=True), "OSError"),
            (self.fake_get(truncated=True), "MAX_PAGES"),
        ):
            with self.subTest(expected=expected):
                recs, _, error = AeroApiFetcher(aero_config(), "UA", get=get).fetch(NOW)
                self.assertIsNone(recs)
                self.assertIn(expected, error or "")
                self.assertNotIn(AERO_ENV["FLIGHTAWARE_API_KEY"], error or "")


def validation_request(opening: MessageEnvelope) -> AssessmentRequest:
    from dataclasses import replace

    envelope = replace(
        opening,
        message_id="validation-request-1",
        sender_agent=AgentName.VALIDATION,
        created_at_utc=NOW + timedelta(seconds=1),
        valid_until_utc=NOW + timedelta(seconds=61),
        correlation_id=opening.message_id,
        reply_to=opening.message_id,
    )
    return AssessmentRequest(envelope, 1)


def weather_reply(request_id: str, opening: MessageEnvelope) -> MessageEnvelope:
    return MessageEnvelope(
        schema_version=1,
        message_id="weather-reply-1",
        case_id=opening.case_id,
        sender_agent=AgentName.WEATHER,
        created_at_utc=NOW + timedelta(seconds=3),
        snapshot_id=opening.snapshot_id,
        evidence_ids=(),
        airport_icao="KDTW",
        valid_until_utc=NOW + timedelta(minutes=1),
        reply_to=request_id,
    )


class ServiceWithFlightsTests(TestCase):
    def candidate(self, service: CongestionService):  # type: ignore[no-untyped-def]
        opening = service.start_cycle(FeedSnapshot(None, None, (), records(), NOW), NOW)
        weather = service.on_assessment_request(
            validation_request(opening), NOW + timedelta(seconds=2)
        )
        service.on_weather_reply(
            weather_reply(weather.envelope.message_id, opening),
            "no_material_impact",
            "wx-1",
            NOW + timedelta(seconds=3),
        )
        (ready,) = service.advance(NOW + timedelta(seconds=4)).ready
        return opening, service.candidates[ready.candidate_id]

    def test_flight_records_become_demand_evidence(self) -> None:
        opening, candidate = self.candidate(CongestionService())
        self.assertIn("flights-20261004T160000Z", opening.evidence_ids)
        names = {m.name: m.value for m in candidate.traffic_metrics}
        self.assertEqual(names["departure_demand_confirmed"], 1)
        self.assertIsNone(candidate.projection_id)  # no capacity configured: no projection

    def test_configured_capacity_produces_a_projection_from_the_backlog(self) -> None:
        capacity = CapacityScenario("operator", 30, CapacityBasis.OPERATOR_CONFIGURED)
        service = CongestionService(projection=ProjectionAssumptions(capacity=capacity))
        _, candidate = self.candidate(service)
        self.assertEqual(candidate.projection_id, "proj-20261004T160000Z")
        projected = [m for m in candidate.traffic_metrics if m.basis is MetricBasis.PROJECTED]
        self.assertEqual(len(projected), 3)
        joined = " ".join(candidate.assumptions)
        self.assertIn("runway queue = departures pushed back", joined)
        self.assertIn("operator_configured", joined)

    def test_projection_horizon_must_match(self) -> None:
        capacity = CapacityScenario("operator", 30, CapacityBasis.OPERATOR_CONFIGURED)
        with self.assertRaises(ValueError):
            CongestionService(
                horizon_minutes=15,
                projection=ProjectionAssumptions(capacity=capacity, horizon_minutes=30),
            )


class AgentConfigTests(TestCase):
    BASE = {
        "CONGESTION_AGENT_SEED": "watchtower TEST ONLY congestion identity",
        "WEATHER_AGENT_ADDRESS": "test-weather-address",
        "VALIDATION_AGENT_ADDRESS": "test-validation-address",
        "DATA_USER_AGENT": "WatchTower-tests (TEST)",
    }

    def test_aeroapi_and_capacity_are_off_by_default(self) -> None:
        config = AgentConfig.from_env(self.BASE)
        self.assertIsNone(config.aeroapi)
        self.assertIsNone(config.projection_assumptions())

    def test_enabled_values_are_read_and_validated(self) -> None:
        config = AgentConfig.from_env(
            {**self.BASE, **AERO_ENV, "DEPARTURE_CAPACITY_PER_HOUR": "36"}
        )
        self.assertIsNotNone(config.aeroapi)
        assumptions = config.projection_assumptions()
        assert assumptions and assumptions.capacity
        self.assertEqual(assumptions.capacity.effective_capacity_per_hour, 36)
        self.assertEqual(assumptions.capacity.basis, CapacityBasis.OPERATOR_CONFIGURED)
        for bad in (
            {"DEPARTURE_CAPACITY_PER_HOUR": "lots"},
            {**AERO_ENV, "FLIGHTAWARE_BUDGET_USD": ""},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                AgentConfig.from_env({**self.BASE, **bad})
