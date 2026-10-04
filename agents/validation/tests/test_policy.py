"""Bounded actions, parameter limits, status/weather rules and untrusted text (TEST data)."""

from dataclasses import replace
from unittest import TestCase

from agents.validation import policy
from agents.validation.packet import EvidenceKind, Finding, Severity, ValidationConfig
from shared.contracts import (
    ActionParameter,
    ActionType,
    AdvisoryType,
    CandidateStatus,
    EvidenceQuality,
    FlowCandidate,
)
from tests.fixtures import validation as fx

CONFIG = ValidationConfig()
WINDOW = (
    ActionParameter("window_start_minute", 0, "minutes after snapshot"),
    ActionParameter("window_end_minute", 15, "minutes after snapshot"),
)


def codes(findings: tuple[Finding, ...]) -> dict[str, Severity]:
    return {f.code: f.severity for f in findings}


def parameter_codes(candidate: FlowCandidate) -> dict[str, Severity]:
    return codes(policy.check_parameters(fx.packet(candidate), CONFIG))


def with_parameters(*parameters: ActionParameter) -> FlowCandidate:
    return replace(fx.actionable_candidate(), proposed_action_parameters=parameters)


def weather_review() -> FlowCandidate:
    return replace(
        fx.actionable_candidate(),
        issue_type=AdvisoryType.WEATHER_FLOW_REVIEW,
        resource="KDTW",
        proposed_action_type=ActionType.REVIEW_FLOW_WINDOW,
        proposed_action_parameters=WINDOW,
    )


def quiet(status: CandidateStatus) -> FlowCandidate:
    candidate = fx.information_candidate()
    return replace(
        candidate,
        status=status,
        proposed_action_type=None,
        quality=EvidenceQuality.UNAVAILABLE
        if status is CandidateStatus.INSUFFICIENT_DATA
        else EvidenceQuality.LIMITED,
    )


class CatalogTests(TestCase):
    def test_every_advisory_type_has_exactly_one_action(self) -> None:
        self.assertEqual(set(policy.ACTION_FOR_ISSUE), set(AdvisoryType))

    def test_action_outside_the_issue_catalog_is_rejected(self) -> None:
        candidate = replace(
            fx.actionable_candidate(), proposed_action_type=ActionType.REVIEW_FLOW_WINDOW
        )
        self.assertEqual(
            codes(policy.check_catalog(candidate)), {"action_not_in_catalog": Severity.INTEGRITY}
        )

    def test_information_only_always_becomes_a_notice(self) -> None:
        candidate = replace(fx.information_candidate(), proposed_action_type=None)
        self.assertIs(policy.proposed_action(candidate), ActionType.INFORMATION_NOTICE)
        self.assertIsNone(policy.proposed_action(quiet(CandidateStatus.NO_CHANGE)))


class ParameterTests(TestCase):
    def test_supported_parameters_pass(self) -> None:
        self.assertEqual(parameter_codes(fx.actionable_candidate()), {})
        self.assertEqual(parameter_codes(weather_review()), {})

    def test_unknown_unit_type_and_missing_parameters_are_rejected(self) -> None:
        cap = ActionParameter("release_cap", 30, "aircraft/hour")
        for parameters, code in (
            ((cap, *WINDOW, ActionParameter("taxi_route", 1, "route")), "unknown_parameter"),
            ((ActionParameter("release_cap", 30, "aircraft/minute"), *WINDOW), "parameter_unit"),
            (
                (ActionParameter("release_cap", 30.0, "aircraft/hour"), *WINDOW),
                "parameter_not_integer",
            ),
            (WINDOW, "missing_parameter"),
        ):
            with self.subTest(code=code):
                self.assertEqual(
                    parameter_codes(with_parameters(*parameters))[code], Severity.INTEGRITY
                )

    def test_release_cap_stays_within_model_bounds_and_capacity(self) -> None:
        for value in (5, 31, 500):
            with self.subTest(value=value):
                candidate = with_parameters(
                    ActionParameter("release_cap", value, "aircraft/hour"), *WINDOW
                )
                self.assertIn("release_cap_out_of_bounds", parameter_codes(candidate))

    def test_window_stays_within_the_horizon(self) -> None:
        cap = ActionParameter("release_cap", 30, "aircraft/hour")
        for start, end in ((0, 16), (10, 10), (12, 5)):
            with self.subTest(start=start, end=end):
                candidate = with_parameters(
                    cap,
                    ActionParameter("window_start_minute", start, "minutes after snapshot"),
                    ActionParameter("window_end_minute", end, "minutes after snapshot"),
                )
                self.assertIn("window_out_of_bounds", parameter_codes(candidate))


class StatusAndRequirementTests(TestCase):
    def test_quiet_statuses_produce_no_advisory(self) -> None:
        insufficient = replace(
            quiet(CandidateStatus.INSUFFICIENT_DATA), required_missing_inputs=("demand",)
        )
        self.assertEqual(
            codes(policy.check_status(insufficient)),
            {"candidate_insufficient_data": Severity.MISSING},
        )
        self.assertEqual(
            codes(policy.check_status(quiet(CandidateStatus.NO_CHANGE))),
            {"no_change": Severity.NO_ADVISORY},
        )

    def test_release_review_needs_a_projection_and_demand(self) -> None:
        candidate = fx.actionable_candidate()
        unprojected = replace(
            candidate,
            projection_id=None,
            traffic_metrics=candidate.traffic_metrics[:2],
        )
        found = codes(policy.check_requirements(fx.packet(unprojected)))
        self.assertEqual(found, {"projection_required": Severity.MISSING})
        records = fx.evidence()
        records[fx.DEMAND_ID] = replace(records[fx.DEMAND_ID], kind=EvidenceKind.TRAFFIC)
        found = codes(policy.check_requirements(fx.packet(evidence=records)))
        self.assertEqual(found, {"demand_required": Severity.MISSING})

    def test_restriction_is_not_attached_without_flight_association(self) -> None:
        restriction = replace(
            fx.information_candidate(),
            status=CandidateStatus.CHANGE_WORTH_REVIEWING,
            issue_type=AdvisoryType.DOWNSTREAM_RESTRICTION,
            proposed_action_type=ActionType.COORDINATE_DOWNSTREAM_RESTRICTION,
            affected_flight_ids=("TEST-FLIGHT-1",),
        )
        found = codes(policy.check_requirements(fx.packet(restriction)))
        self.assertEqual(found, {"restriction_unsupported": Severity.MISSING})


class WeatherTests(TestCase):
    def test_clear_weather_never_blocks_a_traffic_candidate(self) -> None:
        self.assertEqual(policy.check_weather(fx.packet()), ())

    def test_weather_review_needs_a_reported_impact(self) -> None:
        for status in ("no_material_impact", "insufficient_data"):
            with self.subTest(status=status):
                packet = fx.packet(weather_review(), weather=fx.weather(status))
                self.assertEqual(
                    codes(policy.check_weather(packet)),
                    {"weather_contradiction": Severity.RECOVERABLE},
                )
        packet = fx.packet(weather_review(), weather=fx.weather("impact"))
        self.assertEqual(policy.check_weather(packet), ())

    def test_ignored_weather_impact_asks_for_reassessment(self) -> None:
        packet = fx.packet(weather=fx.weather("impact"))
        self.assertEqual(
            codes(policy.check_weather(packet)),
            {"weather_impact_not_reflected": Severity.RECOVERABLE},
        )

    def test_unavailable_weather_limits_traffic_but_blocks_a_weather_review(self) -> None:
        self.assertEqual(
            codes(policy.check_weather(fx.packet(weather=None))),
            {"weather_assessment_unavailable": Severity.LIMITATION},
        )
        self.assertEqual(
            codes(policy.check_weather(fx.packet(weather_review(), weather=None))),
            {"weather_assessment_missing": Severity.MISSING},
        )
        packet = fx.packet(weather=fx.weather("insufficient_data"))
        self.assertEqual(
            codes(policy.check_weather(packet)), {"weather_insufficient": Severity.LIMITATION}
        )


class UntrustedTextTests(TestCase):
    def test_control_characters_or_oversized_text_are_rejected(self) -> None:
        for assumption in ("\x1b]8;;evil\x07", "x" * 501, "line\nbreak"):
            with self.subTest(assumption=assumption[:10]):
                candidate = replace(fx.actionable_candidate(), assumptions=(assumption,))
                self.assertEqual(
                    codes(policy.check_untrusted_text(candidate, CONFIG)),
                    {"untrusted_text_invalid": Severity.INTEGRITY},
                )

    def test_quality_is_never_raised(self) -> None:
        limitation = Finding("aging_traffic", Severity.LIMITATION, "old")
        adequate = fx.actionable_candidate()
        limited = fx.information_candidate()
        self.assertIs(policy.assess_quality(adequate, ()), EvidenceQuality.ADEQUATE)
        self.assertIs(policy.assess_quality(adequate, (limitation,)), EvidenceQuality.LIMITED)
        self.assertIs(policy.assess_quality(limited, ()), EvidenceQuality.LIMITED)
