"""Projection verification: recomputation, invariants and projected numbers (TEST data)."""

from dataclasses import replace
from unittest import TestCase

from agents.validation.calculations import check_projection
from agents.validation.packet import Severity
from projection.aggregate_flow import FlowSnapshot, compare
from projection.assumptions import CapacityBasis, ProjectionAssumptions, ResponseParameters
from shared.contracts import ActionParameter, MetricBasis, TrafficMetric
from tests.fixtures import validation as fx


def codes(packet: object) -> dict[str, Severity]:
    return {f.code: f.severity for f in check_projection(packet)}  # type: ignore[arg-type]


class ProjectionCheckTests(TestCase):
    def test_reproducible_projection_passes(self) -> None:
        self.assertEqual(codes(fx.packet()), {})

    def test_candidates_without_projection_need_no_projection(self) -> None:
        self.assertEqual(codes(fx.packet(fx.information_candidate())), {})

    def test_projected_values_need_a_projection_reference(self) -> None:
        candidate = replace(fx.actionable_candidate(), projection_id=None)
        found = codes(fx.packet(candidate))
        self.assertEqual(found, {"projected_metric_without_projection": Severity.INTEGRITY})

    def test_unloaded_or_different_projection(self) -> None:
        self.assertEqual(
            codes(fx.packet(projection=None)), {"projection_missing": Severity.MISSING}
        )
        other = replace(fx.projection(), projection_id="other-projection")
        found = codes(fx.packet(projection=other))
        self.assertEqual(found, {"projection_reference_mismatch": Severity.INTEGRITY})

    def test_projection_from_another_snapshot_is_rejected(self) -> None:
        packet = fx.projection()
        snapshot = replace(packet.snapshot, snapshot_id="older-snapshot")
        comparison = compare(snapshot, packet.assumptions, ResponseParameters(30, 0, 15))
        moved = replace(packet, snapshot=snapshot, comparison=comparison)
        found = codes(fx.packet(projection=moved))
        self.assertEqual(found, {"projection_snapshot_mismatch": Severity.INTEGRITY})

    def test_an_unavailable_projection_cannot_back_a_candidate(self) -> None:
        packet = fx.projection()
        assumptions = ProjectionAssumptions(capacity=None)
        comparison = compare(packet.snapshot, assumptions, ResponseParameters(30, 0, 15))
        unavailable = replace(packet, assumptions=assumptions, comparison=comparison)
        found = codes(fx.packet(projection=unavailable))
        self.assertEqual(found, {"projection_not_computed": Severity.INTEGRITY})

    def test_tampered_series_breaks_reproduction_and_conservation(self) -> None:
        packet = fx.projection()
        proposal = packet.comparison.proposal
        assert proposal is not None
        served = (proposal.series.served[0] + 1, *proposal.series.served[1:])
        tampered_branch = replace(proposal, series=replace(proposal.series, served=served))
        tampered = replace(packet, comparison=replace(packet.comparison, proposal=tampered_branch))
        found = codes(fx.packet(projection=tampered))
        self.assertEqual(found["projection_not_reproducible"], Severity.INTEGRITY)
        self.assertEqual(found["projection_invariant"], Severity.INTEGRITY)

    def test_baseline_must_have_no_response(self) -> None:
        packet = fx.projection()
        baseline = packet.comparison.baseline
        assert baseline is not None
        altered = replace(baseline, response=ResponseParameters(20, 0, 15))
        tampered = replace(packet, comparison=replace(packet.comparison, baseline=altered))
        self.assertIn("baseline_altered", codes(fx.packet(projection=tampered)))

    def test_projected_numbers_must_match_the_recomputation(self) -> None:
        candidate = fx.actionable_candidate()
        metrics = tuple(
            replace(m, value=m.value + 1) if m.name == "projected_total_waiting_change" else m
            for m in candidate.traffic_metrics
        )
        found = codes(fx.packet(replace(candidate, traffic_metrics=metrics)))
        self.assertEqual(found, {"projected_value_mismatch": Severity.INTEGRITY})

        invented = TrafficMetric(
            "projected_fuel_saved", 120, "kg", MetricBasis.PROJECTED, (fx.PROJECTION_ID,)
        )
        edited = replace(candidate, traffic_metrics=(*candidate.traffic_metrics, invented))
        self.assertEqual(codes(fx.packet(edited)), {"unknown_projected_metric": Severity.INTEGRITY})

    def test_parameters_must_be_the_projected_response(self) -> None:
        candidate = fx.actionable_candidate()
        params = (
            ActionParameter("release_cap", 24, "aircraft/hour"),
            *candidate.proposed_action_parameters[1:],
        )
        found = codes(fx.packet(replace(candidate, proposed_action_parameters=params)))
        self.assertEqual(found, {"parameters_differ_from_projection": Severity.INTEGRITY})

    def test_hypothetical_capacity_is_a_limitation(self) -> None:
        packet = fx.projection(capacity_basis=CapacityBasis.HYPOTHETICAL)
        found = codes(fx.packet(fx.actionable_candidate(packet), projection_packet=packet))
        self.assertEqual(found, {"hypothetical_capacity": Severity.LIMITATION})

    def test_instant_transfer_schematic_is_labelled(self) -> None:
        packet = fx.projection()
        assumptions = replace(packet.assumptions, transit_delay_weights=(1,))
        snapshot: FlowSnapshot = packet.snapshot
        comparison = compare(snapshot, assumptions, ResponseParameters(30, 0, 15))
        simple = replace(packet, assumptions=assumptions, comparison=comparison)
        candidate = fx.actionable_candidate(simple)
        found = codes(fx.packet(candidate, projection_packet=simple))
        self.assertEqual(found, {"simplified_transfer": Severity.LIMITATION})
