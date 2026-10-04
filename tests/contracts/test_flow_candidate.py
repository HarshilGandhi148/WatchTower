"""FlowCandidate structural rules: provenance, bounded actions, honest missing data."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import TestCase

from shared.contracts import (
    ActionParameter,
    ActionType,
    AdvisoryType,
    AgentName,
    CandidateStatus,
    EvidenceQuality,
    FlowCandidate,
    MessageEnvelope,
    MetricBasis,
    TrafficMetric,
)

NOW = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)


def envelope(sender: AgentName = AgentName.CONGESTION) -> MessageEnvelope:
    return MessageEnvelope(
        schema_version=1,
        message_id="msg-1",
        case_id="case-1",
        sender_agent=sender,
        created_at_utc=NOW,
        snapshot_id="snap-1",
        evidence_ids=("adsb-1", "faa-1", "sched-1"),
        airport_icao="KDTW",
        valid_until_utc=NOW + timedelta(minutes=2),
    )


def actionable() -> FlowCandidate:
    return FlowCandidate(
        envelope=envelope(),
        status=CandidateStatus.CHANGE_WORTH_REVIEWING,
        issue_type=AdvisoryType.DEPARTURE_RELEASE_REVIEW,
        resource="KDTW departures",
        window_start_utc=NOW,
        window_end_utc=NOW + timedelta(minutes=15),
        quality=EvidenceQuality.LIMITED,
        affected_flight_ids=("flight-1",),
        traffic_metrics=(
            TrafficMetric("departure_demand", 12, "aircraft", MetricBasis.ESTIMATED, ("sched-1",)),
        ),
        proposed_action_type=ActionType.REVIEW_RELEASE_RATE,
        proposed_action_parameters=(ActionParameter("release_cap", 30, "aircraft/hour"),),
        projection_id="proj-1",
        assumptions=("capacity is hypothetical",),
    )


class FlowCandidateTests(TestCase):
    def test_valid_candidate_is_immutable(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            actionable().status = CandidateStatus.NO_CHANGE  # type: ignore[misc]

    def test_must_come_from_congestion(self) -> None:
        for sender in (AgentName.WEATHER, AgentName.VALIDATION):
            with self.subTest(sender=sender), self.assertRaises(ValueError):
                replace(actionable(), envelope=envelope(sender))

    def test_window_must_be_utc_and_forward(self) -> None:
        cases: list[dict[str, Any]] = [
            {"window_start_utc": datetime(2026, 10, 3, 16)},
            {"window_end_utc": datetime.fromisoformat("2026-10-03T12:15:00-04:00")},
            {"window_end_utc": NOW},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(actionable(), **changes)

    def test_metric_evidence_must_be_in_the_envelope(self) -> None:
        bad = TrafficMetric("count", 3, "aircraft", MetricBasis.OBSERVED, ("invented-1",))
        with self.assertRaises(ValueError):
            replace(actionable(), traffic_metrics=(bad,))

    def test_metric_without_evidence_unit_or_finite_value_is_rejected(self) -> None:
        for args in (
            ("count", 3, "aircraft", MetricBasis.OBSERVED, ()),
            ("count", 3, " ", MetricBasis.OBSERVED, ("adsb-1",)),
            ("count", float("nan"), "aircraft", MetricBasis.OBSERVED, ("adsb-1",)),
            ("count", True, "aircraft", MetricBasis.OBSERVED, ("adsb-1",)),
            ("count", 3, "aircraft", "guess", ("adsb-1",)),
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                TrafficMetric(*args)  # type: ignore[arg-type]

    def test_actionable_needs_an_action_and_some_evidence(self) -> None:
        with self.assertRaises(ValueError):
            replace(actionable(), proposed_action_type=None)
        with self.assertRaises(ValueError):
            replace(actionable(), quality=EvidenceQuality.UNAVAILABLE)

    def test_non_actionable_statuses_carry_no_action_parameters(self) -> None:
        for status in (CandidateStatus.NO_CHANGE, CandidateStatus.INFORMATION_ONLY):
            with self.subTest(status=status), self.assertRaises(ValueError):
                replace(actionable(), status=status, proposed_action_type=None)

    def test_information_only_may_only_propose_a_notice(self) -> None:
        notice = replace(
            actionable(),
            status=CandidateStatus.INFORMATION_ONLY,
            proposed_action_type=ActionType.INFORMATION_NOTICE,
            proposed_action_parameters=(),
        )
        with self.assertRaises(ValueError):
            replace(notice, proposed_action_type=ActionType.REVIEW_RELEASE_RATE)

    def test_insufficient_data_names_missing_inputs_and_proposes_nothing(self) -> None:
        base: dict[str, Any] = {
            "status": CandidateStatus.INSUFFICIENT_DATA,
            "proposed_action_type": None,
            "proposed_action_parameters": (),
            "projection_id": None,
            "quality": EvidenceQuality.UNAVAILABLE,
        }
        with self.assertRaises(ValueError):
            replace(actionable(), **base)
        ok = replace(actionable(), required_missing_inputs=("airport-associated demand",), **base)
        self.assertEqual(ok.required_missing_inputs, ("airport-associated demand",))
        with self.assertRaises(ValueError):
            replace(ok, proposed_action_type=ActionType.REVIEW_FLOW_WINDOW)

    def test_clear_weather_congestion_candidate_needs_no_weather_reference(self) -> None:
        self.assertIsNone(actionable().weather_assessment_id)

    def test_downstream_restriction_needs_faa_event_and_associated_flights(self) -> None:
        base = replace(
            actionable(),
            issue_type=AdvisoryType.DOWNSTREAM_RESTRICTION,
            proposed_action_type=ActionType.COORDINATE_DOWNSTREAM_RESTRICTION,
            proposed_action_parameters=(),
            faa_event_ids=("faa-1",),
        )
        with self.assertRaises(ValueError):
            replace(base, faa_event_ids=())
        with self.assertRaises(ValueError):
            replace(base, affected_flight_ids=())
        with self.assertRaises(ValueError):
            replace(base, faa_event_ids=("faa-unknown",))

    def test_collections_must_be_unique_immutable_tuples(self) -> None:
        cases: list[dict[str, Any]] = [
            {"affected_flight_ids": ["flight-1"]},
            {"affected_flight_ids": ("flight-1", "flight-1")},
            {"affected_flight_ids": ("",)},
            {"assumptions": ["x"]},
            {"tradeoffs": ("",)},
            {"proposed_action_type": "turn_aircraft"},
            {
                "proposed_action_parameters": (
                    ActionParameter("a", 1, "u"),
                    ActionParameter("a", 2, "u"),
                )
            },
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(actionable(), **changes)

    def test_action_catalog_has_no_aircraft_control_actions(self) -> None:
        for forbidden in ("turn", "altitude", "clear", "runway_assignment"):
            self.assertFalse(any(forbidden in action.value for action in ActionType))
