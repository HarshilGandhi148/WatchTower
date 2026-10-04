"""Real congestion candidates pass the validator's domain checks end to end (TEST inputs).

Cross-owner integration test: the real CongestionService builds a candidate from TEST
ADS-B and AeroAPI fixtures, the real Orchestrator routes it, and validate_packet checks
it. Until congestion stores its projection inputs and evidence records, this test
rebuilds them with congestion's public deterministic functions and the placeholder
evidence IDs congestion uses today. No agents, servers or providers are started.
"""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import TestCase

from agents.congestion.candidates import ReviewThresholds, flow_snapshot_from_demand, project
from agents.congestion.feeds import FeedSnapshot
from agents.congestion.metrics import demand_windows, departure_backlog
from agents.congestion.service import CongestionService
from agents.validation.orchestrator import Orchestrator
from agents.validation.packet import (
    EvidenceKind,
    EvidenceRecord,
    ProjectionPacket,
    ValidationPacket,
    WeatherFinding,
)
from agents.validation.service import validate_packet
from data.adsb import parse_point_response
from data.flightaware import Endpoint, parse_flights
from projection.assumptions import (
    CapacityBasis,
    CapacityScenario,
    ProjectionAssumptions,
    ResponseParameters,
)
from shared.contracts import (
    ActionType,
    AdvisoryType,
    AgentName,
    CandidateStatus,
    MessageEnvelope,
    MetricBasis,
)
from shared.orchestration import CasePhase, ValidationVerdict
from tests.fixtures.orchestration import TEST_ADDRESSES

ROOT = Path(__file__).resolve().parents[2]
FIXTURES = ROOT / "tests" / "fixtures"
T0 = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)  # the AeroAPI fixture times are fixed
CONGESTION = TEST_ADDRESSES[AgentName.CONGESTION]
CAPACITY = 6  # TEST operator capacity; the fixture backlog makes a release cap helpful
# Demo threshold low enough for the small TEST backlog (its projected peak is 2 aircraft).
THRESHOLDS = ReviewThresholds(peak_runway_queue_aircraft=2)
KIND_BY_PREFIX = {
    "adsb-": EvidenceKind.TRAFFIC,
    "flights-": EvidenceKind.DEMAND,
    "proj-": EvidenceKind.PROJECTION,
    "faa-": EvidenceKind.FAA_EVENT,
}


def flight_records() -> tuple:  # type: ignore[type-arg]
    departures = json.loads((FIXTURES / "flightaware" / "scheduled_departures.json").read_text())
    arrivals = json.loads((FIXTURES / "flightaware" / "scheduled_arrivals.json").read_text())
    arrivals["links"] = None
    return (
        parse_flights(departures, Endpoint.SCHEDULED_DEPARTURES, T0).records
        + parse_flights(arrivals, Endpoint.SCHEDULED_ARRIVALS, T0).records
    )


def feeds() -> FeedSnapshot:
    adsb = json.loads((FIXTURES / "adsb" / "point_response.json").read_bytes())
    return FeedSnapshot(parse_point_response(adsb, T0), None, (), flight_records(), T0)


def assumptions() -> ProjectionAssumptions:
    capacity = CapacityScenario(
        "TEST operator capacity", CAPACITY, CapacityBasis.OPERATOR_CONFIGURED
    )
    return ProjectionAssumptions(capacity=capacity)


def rebuilt_projection(snapshot_id: str, projection_id: str) -> ProjectionPacket:
    """What congestion computed in start_cycle, from the same public functions and inputs."""
    records = flight_records()
    backlog = departure_backlog(records, T0, T0)
    demand = demand_windows(records, T0, T0, 15, exclude_flight_ids=backlog.flight_ids)
    snapshot = flow_snapshot_from_demand(
        snapshot_id, T0, demand, backlog.upstream_waiting, backlog.runway_queue
    )
    comparison = project(snapshot, assumptions(), ResponseParameters(CAPACITY, 0, 15))
    return ProjectionPacket(
        projection_id=projection_id,
        snapshot=snapshot,
        assumptions=assumptions(),
        comparison=comparison,
    )


class CongestionValidationTests(TestCase):
    def run_case(
        self, service: CongestionService, weather_status: str
    ) -> tuple[Orchestrator, ValidationPacket]:
        coordinator = Orchestrator(TEST_ADDRESSES)
        opening = service.start_cycle(feeds(), T0)
        dispatch = coordinator.open_case(CONGESTION, opening, now=T0)
        assert dispatch is not None
        weather_request = service.on_assessment_request(dispatch.request, T0)
        reply = MessageEnvelope(
            schema_version=1,
            message_id="test-weather-reply-1",
            case_id=opening.case_id,
            sender_agent=AgentName.WEATHER,
            created_at_utc=T0,
            snapshot_id=opening.snapshot_id,
            evidence_ids=(),
            airport_icao="KDTW",
            valid_until_utc=T0 + timedelta(minutes=1),
            reply_to=weather_request.envelope.message_id,
        )
        service.on_weather_reply(reply, weather_status, "test-weather-assessment", T0)
        (ready,) = service.advance(T0 + timedelta(seconds=1)).ready
        coordinator.receive_candidate(CONGESTION, ready, now=T0 + timedelta(seconds=1))
        candidate = service.candidates[ready.candidate_id]

        cited = {*candidate.envelope.evidence_ids, *opening.evidence_ids}
        evidence = {
            evidence_id: EvidenceRecord(
                evidence_id=evidence_id,
                kind=next(k for p, k in KIND_BY_PREFIX.items() if evidence_id.startswith(p)),
                airport_icao="KDTW",
                snapshot_id=opening.snapshot_id,
                data_mode="live",  # TEST fixture standing in for a stored live record
                fetched_at_utc=T0,
            )
            for evidence_id in cited
        }
        projection = (
            rebuilt_projection(opening.snapshot_id, candidate.projection_id)
            if candidate.projection_id
            else None
        )
        packet = ValidationPacket(
            opening=opening,
            ready=ready,
            candidate=candidate,
            evidence=evidence,
            weather=WeatherFinding(
                assessment_id="test-weather-assessment",
                case_id=opening.case_id,
                snapshot_id=opening.snapshot_id,
                status=weather_status,
            ),
            projection=projection,
            now_utc=T0 + timedelta(seconds=2),
        )
        return coordinator, packet

    def test_projected_release_review_is_published_with_matching_numbers(self) -> None:
        coordinator, packet = self.run_case(
            CongestionService(projection=assumptions(), thresholds=THRESHOLDS), "no_material_impact"
        )
        candidate = packet.candidate
        assert candidate is not None and packet.projection is not None
        self.assertIs(candidate.status, CandidateStatus.CHANGE_WORTH_REVIEWING)

        outcome = validate_packet(packet)
        self.assertIs(outcome.verdict, ValidationVerdict.PUBLISH, outcome.findings)
        advisory = outcome.advisory
        assert advisory is not None
        self.assertIs(advisory.advisory_type, AdvisoryType.DEPARTURE_RELEASE_REVIEW)
        self.assertIs(advisory.action_type, ActionType.REVIEW_RELEASE_RATE)
        self.assertIn(candidate.projection_id, advisory.evidence_ids)
        comparison = packet.projection.comparison
        assert comparison.baseline and comparison.proposal
        for metric in candidate.traffic_metrics:
            if metric.basis is MetricBasis.PROJECTED and "peak" in metric.name:
                self.assertIn(f"{metric.value}", advisory.reason)
        self.assertIn(f"{CAPACITY} aircraft/hour", advisory.suggested_response)

        coordinator.finish_validation(packet.ready, outcome.verdict, now=T0 + timedelta(seconds=2))
        case = coordinator.get_case(candidate.envelope.case_id)
        self.assertIs(case.phase, CasePhase.READY_TO_PUBLISH)

    def test_tampered_congestion_numbers_are_rejected(self) -> None:
        _, packet = self.run_case(
            CongestionService(projection=assumptions(), thresholds=THRESHOLDS), "no_material_impact"
        )
        candidate = packet.candidate
        assert candidate is not None
        metrics = tuple(
            replace(m, value=m.value - 1) if m.name == "projected_peak_runway_queue_baseline" else m
            for m in candidate.traffic_metrics
        )
        outcome = validate_packet(
            replace(packet, candidate=replace(candidate, traffic_metrics=metrics))
        )
        self.assertIs(outcome.verdict, ValidationVerdict.REJECT)
        self.assertIn("projected_value_mismatch", {f.code for f in outcome.findings})

    def test_without_capacity_the_candidate_is_not_a_flow_action(self) -> None:
        _, packet = self.run_case(CongestionService(), "no_material_impact")
        candidate = packet.candidate
        assert candidate is not None
        self.assertIsNone(candidate.projection_id)
        outcome = validate_packet(packet)
        self.assertIn(outcome.verdict, (ValidationVerdict.PUBLISH, ValidationVerdict.REJECT))
        if outcome.advisory is not None:
            self.assertIs(outcome.advisory.action_type, ActionType.INFORMATION_NOTICE)
            self.assertEqual(outcome.advisory.action_parameters, ())
        else:
            self.assertIn("no_change", {f.code for f in outcome.findings})
