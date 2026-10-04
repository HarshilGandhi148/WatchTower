"""FlowCandidate decisions: supported actions only, honest missing data, no invented facts."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import TestCase

from agents.congestion.candidates import (
    CandidateInputs,
    EvidenceRefs,
    ReviewThresholds,
    WeatherSignal,
    build_candidate,
    flow_snapshot_from_demand,
    project,
)
from agents.congestion.metrics import NearbyTraffic, demand_windows
from data.faa_status import parse_status_xml
from data.flightaware import Association, DemandRecord, FlightDirection, TimeKind
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
    EvidenceQuality,
    MessageEnvelope,
    MetricBasis,
)

NOW = datetime(2026, 10, 4, 1, 0, tzinfo=UTC)
FETCHED = NOW - timedelta(minutes=1)
ENVELOPE = MessageEnvelope(
    schema_version=1,
    message_id="msg-1",
    case_id="case-1",
    sender_agent=AgentName.CONGESTION,
    created_at_utc=NOW,
    snapshot_id="snap-1",
    evidence_ids=(),
    airport_icao="KDTW",
    valid_until_utc=NOW + timedelta(minutes=2),
)
DEFAULT_RESPONSE = ResponseParameters(30, 0, 15)
REFS = EvidenceRefs(traffic="adsb-1", demand="flights-1", projection_id="proj-1")


def nearby(
    total: int | None = 10, quality: EvidenceQuality = EvidenceQuality.ADEQUATE
) -> NearbyTraffic:
    if total is None:
        return NearbyTraffic(
            None, None, None, None, 0, 0, 0, 40.0, EvidenceQuality.UNAVAILABLE, ("no snapshot",)
        )
    return NearbyTraffic(total, total, 0, 0, 0, 0, 0, 40.0, quality)


def departures(
    count: int, *, association: Association = Association.CONFIRMED_RECORD
) -> tuple[DemandRecord, ...]:
    return tuple(
        DemandRecord(
            f"f{i}",
            FlightDirection.DEPARTURE,
            NOW + timedelta(minutes=1),
            TimeKind.ESTIMATED_RUNWAY,
            association,
        )  # fmt: skip
        for i in range(count)
    )


def assumptions(rate: int = 30, basis: CapacityBasis = CapacityBasis.OPERATOR_CONFIGURED):
    return ProjectionAssumptions(capacity=CapacityScenario("base", rate, basis))


def make(
    records: tuple[DemandRecord, ...] | None = departures(20),
    *,
    response: ResponseParameters | None = DEFAULT_RESPONSE,
    backlog: int | None = 0,
    rate: int = 30,
    basis: CapacityBasis = CapacityBasis.OPERATOR_CONFIGURED,
    **changes: Any,
) -> CandidateInputs:
    windows = demand_windows(records, FETCHED if records is not None else None, NOW)
    comparison = None
    if response is not None:
        snap = flow_snapshot_from_demand("snap-1", NOW, windows, backlog, backlog)
        comparison = project(snap, assumptions(rate, basis), response)
    base = CandidateInputs(
        envelope=ENVELOPE,
        now_utc=NOW,
        horizon_minutes=15,
        nearby=nearby(),
        demand=windows,
        evidence=REFS,
        weather_signal=WeatherSignal.NO_MATERIAL_IMPACT,
        comparison=comparison,
    )
    return replace(base, **changes)


FAA_XML = (
    b"<AIRPORT_STATUS_INFORMATION><Update_Time>Sun Oct 4 00:55:00 2026 GMT</Update_Time>"
    b"<Delay_type><Name>Airport Closures</Name><Airport_Closure_List>"
    b"<Airport><ARPT>DTW</ARPT><Reason>TEST</Reason><Start>Oct 4 at 00:00 UTC.</Start></Airport>"
    b"<Airport><ARPT>EWR</ARPT><Reason>TEST</Reason></Airport>"
    b"</Airport_Closure_List></Delay_type></AIRPORT_STATUS_INFORMATION>"
)


class ActionableTests(TestCase):
    def test_clear_weather_congestion_is_a_departure_release_review(self) -> None:
        c = build_candidate(make())
        self.assertEqual(c.status, CandidateStatus.CHANGE_WORTH_REVIEWING)
        self.assertEqual(c.issue_type, AdvisoryType.DEPARTURE_RELEASE_REVIEW)
        self.assertEqual(c.proposed_action_type, ActionType.REVIEW_RELEASE_RATE)
        self.assertEqual(
            {p.name: p.value for p in c.proposed_action_parameters},
            {"release_cap": 30, "window_start_minute": 0, "window_end_minute": 15},
        )
        self.assertEqual(c.projection_id, "proj-1")
        self.assertIsNone(c.weather_assessment_id)
        self.assertEqual(c.resource, "KDTW departures")
        self.assertEqual(c.window_end_utc - c.window_start_utc, timedelta(minutes=15))

    def test_evidence_is_exactly_what_was_used_and_every_metric_cites_it(self) -> None:
        c = build_candidate(make())
        self.assertEqual(c.envelope.evidence_ids, ("adsb-1", "flights-1", "proj-1"))
        names = {m.name for m in c.traffic_metrics}
        self.assertIn("nearby_aircraft_total", names)
        self.assertIn("departure_demand_confirmed", names)
        projected = [m for m in c.traffic_metrics if m.basis is MetricBasis.PROJECTED]
        self.assertEqual(len(projected), 3)
        self.assertTrue(all(m.evidence_ids == ("proj-1",) for m in projected))

    def test_weather_impact_becomes_a_flow_window_review_with_window_only(self) -> None:
        c = build_candidate(make(weather_signal=WeatherSignal.IMPACT, weather_assessment_id="wx-1"))
        self.assertEqual(c.issue_type, AdvisoryType.WEATHER_FLOW_REVIEW)
        self.assertEqual(c.proposed_action_type, ActionType.REVIEW_FLOW_WINDOW)
        self.assertEqual(c.weather_assessment_id, "wx-1")
        self.assertEqual(
            {p.name for p in c.proposed_action_parameters},
            {"window_start_minute", "window_end_minute"},
        )

    def test_weather_impact_without_assessment_id_is_a_caller_error(self) -> None:
        with self.assertRaises(ValueError):
            build_candidate(make(weather_signal=WeatherSignal.IMPACT))

    def test_hypothetical_capacity_or_unknown_weather_limits_quality(self) -> None:
        operator = build_candidate(make())
        self.assertEqual(operator.quality, EvidenceQuality.ADEQUATE)
        hypo = build_candidate(make(basis=CapacityBasis.HYPOTHETICAL))
        self.assertEqual(hypo.quality, EvidenceQuality.LIMITED)
        unknown = build_candidate(make(weather_signal=WeatherSignal.UNKNOWN))
        self.assertEqual(unknown.quality, EvidenceQuality.LIMITED)

    def test_tradeoffs_are_templated_from_projection_and_never_claim_hidden_savings(self) -> None:
        c = build_candidate(make())
        self.assertEqual(len(c.tradeoffs), 2)
        self.assertIn("peak runway queue", c.tradeoffs[0])
        # Delay moved upstream is shown, not hidden: waiting is reported on both sides.
        self.assertIn("upstream waiting", c.tradeoffs[1])
        self.assertTrue(
            "no reduction in total waiting" in c.tradeoffs[1] or "down" in c.tradeoffs[1]
        )

    def test_assumptions_record_thresholds_capacity_and_model(self) -> None:
        text = " ".join(build_candidate(make()).assumptions)
        for expected in (
            "demo configuration",
            "operator_configured",
            "aggregate-flow-1",
            "not assumed",
        ):
            self.assertIn(expected, text)

    def test_deterministic(self) -> None:
        self.assertEqual(build_candidate(make()), build_candidate(make()))


class NotActionableTests(TestCase):
    def test_proposal_that_does_not_help_is_not_proposed(self) -> None:
        # Cap above peak demand: valid comparison, no benefit, so only a notice.
        c = build_candidate(make(response=ResponseParameters(600, 0, 15)))
        self.assertEqual(c.status, CandidateStatus.INFORMATION_ONLY)
        self.assertEqual(c.proposed_action_type, ActionType.INFORMATION_NOTICE)
        self.assertEqual(c.proposed_action_parameters, ())
        self.assertEqual(c.tradeoffs, ())

    def test_no_proposal_supplied_gives_notice_not_action(self) -> None:
        c = build_candidate(make(response=None, nearby=nearby(30)))
        self.assertEqual(c.status, CandidateStatus.INFORMATION_ONLY)

    def test_quiet_traffic_is_no_change(self) -> None:
        c = build_candidate(make(departures(1), response=ResponseParameters(30, 0, 15)))
        self.assertEqual(c.status, CandidateStatus.NO_CHANGE)
        self.assertIsNone(c.proposed_action_type)

    def test_weather_concern_without_demand_is_information_only(self) -> None:
        c = build_candidate(
            make(
                None,
                response=None,
                weather_signal=WeatherSignal.IMPACT,
                weather_assessment_id="wx-1",
            )
        )
        self.assertEqual(c.status, CandidateStatus.INFORMATION_ONLY)
        self.assertEqual(c.proposed_action_type, ActionType.INFORMATION_NOTICE)
        self.assertTrue(any("airport-associated demand" in m for m in c.required_missing_inputs))

    def test_growing_nearby_traffic_triggers_a_notice(self) -> None:
        c = build_candidate(make(None, response=None, nearby=nearby(12), previous_nearby=nearby(5)))
        self.assertEqual(c.status, CandidateStatus.INFORMATION_ONLY)
        self.assertFalse(any(m.name == "nearby_aircraft_change" for m in c.traffic_metrics))
        self.assertEqual(
            next(m.value for m in c.traffic_metrics if m.name == "nearby_aircraft_total"), 12
        )

    def test_nothing_usable_is_insufficient_data_naming_what_is_missing(self) -> None:
        c = build_candidate(
            make(None, response=None, nearby=nearby(None), weather_signal=WeatherSignal.UNKNOWN)
        )
        self.assertEqual(c.status, CandidateStatus.INSUFFICIENT_DATA)
        self.assertEqual(c.quality, EvidenceQuality.UNAVAILABLE)
        self.assertIsNone(c.proposed_action_type)
        self.assertEqual(c.envelope.evidence_ids, ())
        joined = " ".join(c.required_missing_inputs)
        for expected in ("nearby traffic", "airport-associated demand", "FAA", "weather"):
            self.assertIn(expected, joined)

    def test_unavailable_projection_blocks_action_and_explains_why(self) -> None:
        c = build_candidate(make(backlog=None))
        self.assertNotEqual(c.status, CandidateStatus.CHANGE_WORTH_REVIEWING)
        self.assertIsNone(c.projection_id)
        self.assertFalse(any(m.basis is MetricBasis.PROJECTED for m in c.traffic_metrics))
        self.assertTrue(any("projection:" in m for m in c.required_missing_inputs))

    def test_downstream_restriction_is_never_produced(self) -> None:
        faa = parse_status_xml(FAA_XML, NOW)
        key = faa.events_for_airport("KDTW")[0].event_key
        cases = (
            make(),
            make(weather_signal=WeatherSignal.IMPACT, weather_assessment_id="wx"),
            make(faa=faa, evidence=replace(REFS, faa_events=((key, "faa-1"),))),
            make(None, response=None, nearby=nearby(None)),
        )
        for inputs in cases:
            self.assertNotEqual(
                build_candidate(inputs).issue_type, AdvisoryType.DOWNSTREAM_RESTRICTION
            )


class FaaTests(TestCase):
    def setUp(self) -> None:
        self.faa = parse_status_xml(FAA_XML, NOW)
        self.key = self.faa.events_for_airport("KDTW")[0].event_key

    def test_kdtw_event_is_cited_and_other_airports_are_ignored(self) -> None:
        refs = replace(REFS, faa_events=((self.key, "faa-1"),))
        c = build_candidate(
            make(None, response=None, nearby=nearby(2), faa=self.faa, evidence=refs)
        )
        self.assertEqual(c.status, CandidateStatus.INFORMATION_ONLY)
        self.assertEqual(c.faa_event_ids, ("faa-1",))
        self.assertIn("faa-1", c.envelope.evidence_ids)

    def test_event_without_evidence_id_is_a_caller_error(self) -> None:
        with self.assertRaises(ValueError):
            build_candidate(make(faa=self.faa))

    def test_feed_without_kdtw_events_adds_nothing(self) -> None:
        empty = parse_status_xml(
            b"<AIRPORT_STATUS_INFORMATION><Update_Time>Sun Oct 4 00:55:00 2026 GMT"
            b"</Update_Time></AIRPORT_STATUS_INFORMATION>",
            NOW,
        )
        c = build_candidate(make(departures(1), faa=empty))
        self.assertEqual(c.status, CandidateStatus.NO_CHANGE)
        self.assertFalse(any("FAA" in m for m in c.required_missing_inputs))

    def test_missing_faa_feed_is_listed_as_unknown_not_empty(self) -> None:
        c = build_candidate(make(departures(1)))
        self.assertTrue(
            any("FAA" in m and "not established" in m for m in c.required_missing_inputs)
        )


class InputValidationTests(TestCase):
    def test_used_inputs_need_evidence_ids(self) -> None:
        for refs in (
            EvidenceRefs(demand="d", projection_id="p"),
            EvidenceRefs(traffic="t", projection_id="p"),
            EvidenceRefs(traffic="t", demand="d"),
        ):
            with self.subTest(refs=refs), self.assertRaises(ValueError):
                build_candidate(make(evidence=refs))

    def test_projection_must_match_the_case_snapshot(self) -> None:
        with self.assertRaises(ValueError):
            build_candidate(make(envelope=replace(ENVELOPE, snapshot_id="other")))

    def test_naive_time_and_bad_thresholds_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_candidate(make(now_utc=datetime(2026, 10, 4, 1, 0)))
        with self.assertRaises(ValueError):
            ReviewThresholds(peak_runway_queue_aircraft=0)


class FlowSnapshotTests(TestCase):
    def test_only_confirmed_departures_feed_the_projection(self) -> None:
        records = departures(7) + departures(3, association=Association.INFERRED)
        windows = demand_windows(records, FETCHED, NOW)
        snap = flow_snapshot_from_demand("s", NOW, windows, 0, 0)
        assert snap.demand_per_minute
        self.assertEqual(sum(snap.demand_per_minute), 7)
        self.assertEqual(len(snap.demand_per_minute), 15)
        self.assertIn("not observed gate readiness", snap.demand_basis)

    def test_unavailable_demand_or_backlog_stays_unknown(self) -> None:
        unavailable = flow_snapshot_from_demand("s", NOW, demand_windows(None, None, NOW), 0, 0)
        self.assertIsNone(unavailable.demand_per_minute)
        windows = demand_windows(departures(2), FETCHED, NOW)
        self.assertIsNone(
            flow_snapshot_from_demand("s", NOW, windows, None, 0).initial_upstream_waiting
        )
