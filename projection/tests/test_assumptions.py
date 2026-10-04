"""Bounds and validation for explicit projection assumptions."""

from unittest import TestCase

from projection.assumptions import (
    CapacityBasis,
    CapacityScenario,
    ProjectionAssumptions,
    ResponseParameters,
    check_response,
)


def capacity(rate: int = 60) -> CapacityScenario:
    return CapacityScenario("base", rate, CapacityBasis.OPERATOR_CONFIGURED)


class CapacityScenarioTests(TestCase):
    def test_rejects_bad_capacity_values(self) -> None:
        for rate in (-1, 121, True, 30.5):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                CapacityScenario("x", rate, CapacityBasis.HYPOTHETICAL)  # type: ignore[arg-type]

    def test_requires_name_and_known_basis(self) -> None:
        with self.assertRaises(ValueError):
            CapacityScenario(" ", 10, CapacityBasis.HYPOTHETICAL)
        with self.assertRaises(ValueError):
            CapacityScenario("x", 10, "guess")  # type: ignore[arg-type]


class AssumptionsTests(TestCase):
    def test_horizon_limited_to_plan_values(self) -> None:
        for horizon in (0, 10, 45):
            with self.subTest(horizon=horizon), self.assertRaises(ValueError):
                ProjectionAssumptions(capacity=capacity(), horizon_minutes=horizon)
        ProjectionAssumptions(capacity=capacity(), horizon_minutes=30)

    def test_transit_weights_must_be_usable(self) -> None:
        for weights in ((), (0, 0), (-1, 2), (1, True)):
            with self.subTest(weights=weights), self.assertRaises(ValueError):
                ProjectionAssumptions(capacity=capacity(), transit_delay_weights=weights)

    def test_missing_capacity_is_representable_and_not_defaulted(self) -> None:
        self.assertIsNone(ProjectionAssumptions(capacity=None).capacity)

    def test_instant_transfer_is_flagged(self) -> None:
        instant = ProjectionAssumptions(capacity=None, transit_delay_weights=(1,))
        self.assertTrue(instant.simplified_instant_transfer)
        self.assertFalse(ProjectionAssumptions(capacity=None).simplified_instant_transfer)


class ResponseBoundsTests(TestCase):
    def setUp(self) -> None:
        self.assumptions = ProjectionAssumptions(capacity=capacity())

    def test_baseline_needs_no_bounds_check(self) -> None:
        check_response(ResponseParameters(), self.assumptions)

    def test_restriction_must_have_a_forward_window(self) -> None:
        with self.assertRaises(ValueError):
            ResponseParameters(30, window_start_minute=5, window_end_minute=5)

    def test_cap_below_bound_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            check_response(ResponseParameters(1, 0, 10), self.assumptions)

    def test_window_past_horizon_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            check_response(ResponseParameters(30, 0, 16), self.assumptions)

    def test_valid_restriction_passes(self) -> None:
        check_response(ResponseParameters(30, 2, 12), self.assumptions)
