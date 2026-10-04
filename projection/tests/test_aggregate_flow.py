"""Projection invariants from plan sections 8 and 14: determinism, conservation,
bounded throughput, upstream waiting counted, and unavailable-not-zero."""

from dataclasses import replace
from datetime import UTC, datetime
from unittest import TestCase

from projection.aggregate_flow import (
    BranchResult,
    FlowSnapshot,
    ProjectionStatus,
    apportion,
    compare,
    compare_sensitivity,
    input_fingerprint,
    minute_allocation,
    simulate_branch,
    verify_branch,
)
from projection.assumptions import (
    NO_RESPONSE,
    CapacityBasis,
    CapacityScenario,
    ProjectionAssumptions,
    ResponseParameters,
)

AS_OF = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)


def cap(rate: int, name: str = "base") -> CapacityScenario:
    return CapacityScenario(name, rate, CapacityBasis.HYPOTHETICAL)


def snapshot(
    demand: tuple[int, ...] | None = (2,) * 15,
    upstream: int | None = 0,
    queue: int | None = 0,
) -> FlowSnapshot:
    return FlowSnapshot(
        snapshot_id="snap-1",
        as_of_utc=AS_OF,
        initial_upstream_waiting=upstream,
        initial_runway_queue=queue,
        demand_per_minute=demand,
        demand_basis="schedule estimate (TEST)",
    )


def assumptions(
    rate: int | None = 60, weights: tuple[int, ...] = (0, 0, 1, 2, 1)
) -> ProjectionAssumptions:
    return ProjectionAssumptions(
        capacity=None if rate is None else cap(rate), transit_delay_weights=weights
    )


class HelperTests(TestCase):
    def test_minute_allocation_sums_exactly_to_hourly_rate(self) -> None:
        for rate in (0, 1, 7, 38, 60, 119):
            with self.subTest(rate=rate):
                self.assertEqual(sum(minute_allocation(rate, m) for m in range(60)), rate)

    def test_apportion_conserves_count_and_is_deterministic(self) -> None:
        for count in range(40):
            for weights in ((1,), (0, 0, 1, 2, 1), (1, 1, 1), (3, 0, 5)):
                result = apportion(count, weights)
                self.assertEqual(sum(result), count)
                self.assertEqual(result, apportion(count, weights))
                self.assertTrue(all(r >= 0 for r in result))
                self.assertTrue(all(r == 0 for r, w in zip(result, weights, strict=True) if w == 0))

    def test_apportion_ties_go_to_lower_delay(self) -> None:
        self.assertEqual(apportion(1, (1, 1)), (1, 0))


class SnapshotTests(TestCase):
    def test_rejects_naive_or_non_utc_time(self) -> None:
        with self.assertRaises(ValueError):
            replace(snapshot(), as_of_utc=datetime(2026, 10, 3, 16))

    def test_rejects_negative_or_non_integer_counts(self) -> None:
        for bad in ((1, -1), (1.5,), (True,)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                snapshot(demand=bad)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            snapshot(queue=-1)

    def test_demand_needs_a_basis_label(self) -> None:
        with self.assertRaises(ValueError):
            replace(snapshot(), demand_basis="")


class UnavailableTests(TestCase):
    def check_unavailable(self, snap: FlowSnapshot, assume: ProjectionAssumptions) -> None:
        result = compare(snap, assume, ResponseParameters(30, 0, 10))
        self.assertEqual(result.status, ProjectionStatus.UNAVAILABLE)
        self.assertTrue(result.unavailable_reasons)
        self.assertIsNone(result.baseline)
        self.assertIsNone(result.proposal)
        self.assertIsNone(result.deltas)

    def test_missing_demand_is_not_zero_demand(self) -> None:
        self.check_unavailable(snapshot(demand=None), assumptions())

    def test_short_demand_series_is_unavailable(self) -> None:
        self.check_unavailable(snapshot(demand=(1,) * 10), assumptions())

    def test_unknown_backlog_is_unavailable(self) -> None:
        self.check_unavailable(snapshot(upstream=None), assumptions())
        self.check_unavailable(snapshot(queue=None), assumptions())

    def test_missing_capacity_is_unavailable_not_defaulted(self) -> None:
        self.check_unavailable(snapshot(), assumptions(rate=None))

    def test_simulate_branch_refuses_missing_inputs(self) -> None:
        with self.assertRaises(ValueError):
            simulate_branch(snapshot(demand=None), assumptions(), NO_RESPONSE)

    def test_all_reasons_are_reported(self) -> None:
        result = compare(snapshot(demand=None, upstream=None), assumptions(None), NO_RESPONSE)
        self.assertEqual(len(result.unavailable_reasons), 3)


class InvariantTests(TestCase):
    cases = (
        (snapshot(), assumptions(60), NO_RESPONSE),
        (snapshot(), assumptions(30), ResponseParameters(24, 0, 12)),
        (
            snapshot((9, 0, 0, 5) * 3 + (1, 1, 1), 4, 3),
            assumptions(40),
            ResponseParameters(20, 2, 14),
        ),
        (snapshot(upstream=7, queue=5), assumptions(0), ResponseParameters(12, 0, 15)),
        (snapshot((3,) * 15), assumptions(45, (1,)), ResponseParameters(30, 5, 15)),
        (snapshot((4,) * 15), assumptions(90, (0, 0, 0, 0, 1)), NO_RESPONSE),
    )

    def test_every_branch_passes_independent_verification(self) -> None:
        for snap, assume, response in self.cases:
            result = compare(snap, assume, response)
            assert result.baseline and result.proposal
            for branch in (result.baseline, result.proposal):
                with self.subTest(demand=snap.demand_per_minute, response=response):
                    self.assertEqual(verify_branch(snap, assume, branch), ())

    def test_counts_are_nonnegative_integers(self) -> None:
        for snap, assume, response in self.cases:
            s = simulate_branch(snap, assume, response).series
            for values in (s.released, s.served, s.upstream_waiting, s.runway_queue, s.in_transit):
                self.assertTrue(all(type(v) is int and v >= 0 for v in values))

    def test_zero_demand_invents_no_flights(self) -> None:
        branch = simulate_branch(snapshot((0,) * 15), assumptions(60), NO_RESPONSE)
        self.assertEqual(branch.metrics.total_served, 0)
        self.assertEqual(branch.metrics.total_waiting_aircraft_minutes, 0)
        self.assertEqual(branch.metrics.peak_runway_queue, 0)

    def test_zero_demand_serves_only_existing_backlog(self) -> None:
        snap = snapshot((0,) * 15, upstream=0, queue=5)
        branch = simulate_branch(snap, assumptions(60), NO_RESPONSE)
        self.assertEqual(branch.metrics.total_served, 5)

    def test_served_never_exceeds_capacity_or_available_aircraft(self) -> None:
        busy = simulate_branch(snapshot((10,) * 15), assumptions(30, (1,)), NO_RESPONSE)
        self.assertEqual(busy.metrics.total_served, 30 * 15 // 60)
        quiet = simulate_branch(snapshot((1,) * 15), assumptions(120, (1,)), NO_RESPONSE)
        self.assertEqual(quiet.metrics.total_served, 15)

    def test_zero_capacity_serves_nothing_and_still_conserves(self) -> None:
        snap, assume = snapshot(upstream=3, queue=2), assumptions(0)
        branch = simulate_branch(snap, assume, NO_RESPONSE)
        self.assertEqual(branch.metrics.total_served, 0)
        self.assertEqual(verify_branch(snap, assume, branch), ())

    def test_aircraft_in_transit_are_counted_at_the_horizon(self) -> None:
        snap, assume = snapshot((5,) * 15), assumptions(60, (0, 0, 0, 0, 1))
        branch = simulate_branch(snap, assume, NO_RESPONSE)
        self.assertGreater(branch.metrics.final_in_transit, 0)
        self.assertEqual(verify_branch(snap, assume, branch), ())

    def test_verifier_detects_broken_conservation_and_capacity(self) -> None:
        snap, assume = snapshot(), assumptions(60)
        branch = simulate_branch(snap, assume, NO_RESPONSE)
        queue = (branch.series.runway_queue[0] + 1,) + branch.series.runway_queue[1:]
        lost = BranchResult(NO_RESPONSE, replace(branch.series, runway_queue=queue), branch.metrics)
        self.assertTrue(verify_branch(snap, assume, lost))
        served = (9,) + branch.series.served[1:]
        over = BranchResult(NO_RESPONSE, replace(branch.series, served=served), branch.metrics)
        self.assertTrue(any("capacity" in p for p in verify_branch(snap, assume, over)))

    def test_verifier_detects_swapped_demand(self) -> None:
        snap, assume = snapshot(), assumptions(60)
        branch = simulate_branch(snap, assume, NO_RESPONSE)
        other = BranchResult(NO_RESPONSE, replace(branch.series, demand=(0,) * 15), branch.metrics)
        self.assertTrue(any("demand" in p for p in verify_branch(snap, assume, other)))


class ComparisonTests(TestCase):
    def test_reproducible_for_identical_inputs(self) -> None:
        snap = snapshot((4, 0) * 7 + (4,), 2, 1)
        first = compare(snap, assumptions(45), ResponseParameters(24, 1, 10))
        second = compare(snap, assumptions(45), ResponseParameters(24, 1, 10))
        self.assertEqual(first, second)
        self.assertEqual(len(first.input_fingerprint), 64)

    def test_fingerprint_changes_with_any_input(self) -> None:
        snap, assume, response = snapshot(), assumptions(), ResponseParameters(24, 0, 10)
        base = input_fingerprint(snap, assume, response)
        renamed = replace(snap, snapshot_id="snap-2")
        self.assertNotEqual(base, input_fingerprint(renamed, assume, response))
        self.assertNotEqual(base, input_fingerprint(snap, assumptions(61), response))
        self.assertNotEqual(base, input_fingerprint(snap, assume, ResponseParameters(25, 0, 10)))

    def test_branches_share_demand_and_capacity(self) -> None:
        result = compare(snapshot(), assumptions(30), ResponseParameters(24, 0, 10))
        assert result.baseline and result.proposal
        self.assertEqual(result.baseline.series.demand, result.proposal.series.demand)
        self.assertEqual(result.baseline.response, NO_RESPONSE)
        self.assertEqual(result.capacity, cap(30))
        self.assertEqual(result.model_version, assumptions().model_version)

    def test_identical_response_gives_identical_branches(self) -> None:
        result = compare(snapshot(), assumptions(30), NO_RESPONSE)
        assert result.deltas
        self.assertEqual(result.baseline, result.proposal)
        self.assertEqual(result.deltas.total_waiting_aircraft_minutes, 0)
        self.assertEqual(result.deltas.total_served, 0)

    def test_moving_delay_upstream_is_not_reported_as_removed(self) -> None:
        surge = (5,) + (0,) * 14
        result = compare(snapshot(surge), assumptions(60, (1,)), ResponseParameters(60, 0, 15))
        assert result.deltas
        self.assertLess(result.deltas.runway_waiting_aircraft_minutes, 0)
        self.assertGreater(result.deltas.upstream_waiting_aircraft_minutes, 0)
        self.assertEqual(result.deltas.total_waiting_aircraft_minutes, 0)
        self.assertLess(result.deltas.peak_runway_queue, 0)

    def test_restriction_below_capacity_does_not_improve_total_waiting(self) -> None:
        result = compare(snapshot((3,) * 15), assumptions(60, (1,)), ResponseParameters(30, 0, 15))
        assert result.deltas
        self.assertGreaterEqual(result.deltas.total_waiting_aircraft_minutes, 0)
        self.assertLessEqual(result.deltas.total_served, 0)

    def test_ineffective_restriction_is_still_a_valid_comparison(self) -> None:
        result = compare(snapshot((0,) * 15), assumptions(60), ResponseParameters(30, 0, 15))
        self.assertEqual(result.status, ProjectionStatus.COMPUTED)
        assert result.deltas
        self.assertEqual(result.deltas.total_waiting_aircraft_minutes, 0)

    def test_out_of_bounds_response_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            compare(snapshot(), assumptions(), ResponseParameters(1, 0, 10))

    def test_instant_transfer_flag_propagates_for_labelling(self) -> None:
        instant = compare(snapshot(), assumptions(60, (1,)), NO_RESPONSE)
        self.assertTrue(instant.simplified_instant_transfer)
        self.assertFalse(
            compare(snapshot(), assumptions(60), NO_RESPONSE).simplified_instant_transfer
        )

    def test_lower_capacity_never_reduces_baseline_waiting(self) -> None:
        results = compare_sensitivity(
            snapshot((3,) * 15),
            assumptions(),
            ResponseParameters(30, 0, 15),
            {"low": cap(20, "low"), "base": cap(40, "base"), "high": cap(80, "high")},
        )
        waits = []
        for label in ("low", "base", "high"):
            baseline = results[label].baseline
            assert baseline
            waits.append(baseline.metrics.total_waiting_aircraft_minutes)
        self.assertGreaterEqual(waits[0], waits[1])
        self.assertGreaterEqual(waits[1], waits[2])

    def test_sensitivity_with_missing_demand_stays_unavailable(self) -> None:
        results = compare_sensitivity(
            snapshot(demand=None), assumptions(), NO_RESPONSE, {"low": cap(20), "high": cap(80)}
        )
        self.assertTrue(all(r.status is ProjectionStatus.UNAVAILABLE for r in results.values()))
