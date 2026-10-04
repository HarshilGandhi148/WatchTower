"""AeroAPI demand in the congestion agent: backlog, guarded fetch, stored service inputs."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import TestCase

from agents.congestion.agent import AgentConfig
from agents.congestion.candidates import flow_snapshot_from_demand, project
from agents.congestion.feeds import FeedSnapshot
from agents.congestion.metrics import demand_windows, departure_backlog
from agents.congestion.service import CongestionService
from agents.congestion.tests.test_agent import ENV
from data.flightaware import Endpoint, parse_flights
from projection.aggregate_flow import ProjectionStatus
from projection.assumptions import (
    CapacityBasis,
    CapacityScenario,
    ProjectionAssumptions,
    ResponseParameters,
)
from shared.contracts import AgentName, MessageEnvelope
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
        opening = service.start_cycle(
            FeedSnapshot(
                None,
                None,
                snapshot_id="TEST-stored-snapshot",
                case_id="TEST-case",
                evidence_ids=("TEST-stored-flight-batch",),
                window_start_utc=NOW,
                window_end_utc=NOW + timedelta(minutes=15),
                expires_at_utc=NOW + timedelta(seconds=120),
                demand_records=records(),
                demand_fetched_at_utc=NOW,
                demand_evidence_id="TEST-stored-flight-batch",
            ),
            NOW,
        )
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
        self.assertIn("TEST-stored-flight-batch", opening.evidence_ids)
        names = {m.name: m.value for m in candidate.traffic_metrics}
        self.assertEqual(names["departure_demand_confirmed"], 2)
        self.assertIsNone(candidate.projection_id)  # no capacity configured: no projection

    def test_backlog_projection_is_computed_offline_without_publishing_a_reference(self) -> None:
        capacity = CapacityScenario("operator", 30, CapacityBasis.OPERATOR_CONFIGURED)
        backlog = departure_backlog(records(), NOW, NOW)
        demand = demand_windows(records(), NOW, NOW, exclude_flight_ids=backlog.flight_ids)
        snapshot = flow_snapshot_from_demand(
            "TEST-stored-snapshot", NOW, demand, backlog.upstream_waiting, backlog.runway_queue
        )
        result = project(
            snapshot, ProjectionAssumptions(capacity=capacity), ResponseParameters(30, 0, 15)
        )
        self.assertEqual(result.status, ProjectionStatus.COMPUTED)
        # The service cannot attach this result until a supported projection is persisted.
        _, candidate = self.candidate(CongestionService())
        self.assertIsNone(candidate.projection_id)


class AgentConfigTests(TestCase):
    def test_paid_settings_do_not_activate_polling_or_unstored_projections(self) -> None:
        default = AgentConfig.from_env(ENV)
        configured = AgentConfig.from_env({**ENV, **AERO_ENV, "DEPARTURE_CAPACITY_PER_HOUR": "36"})
        self.assertEqual(default, configured)
