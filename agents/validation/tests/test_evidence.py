"""Reference, provenance, freshness and window checks on loaded packets (TEST data)."""

from collections.abc import Iterable
from dataclasses import replace
from datetime import timedelta
from unittest import TestCase

from agents.validation.evidence import check_evidence, check_references, check_window
from agents.validation.packet import EvidenceKind, Finding, Severity, ValidationConfig
from shared.contracts import MetricBasis, TrafficMetric
from tests.fixtures import validation as fx

CONFIG = ValidationConfig()


def codes(findings: Iterable[Finding]) -> dict[str, Severity]:
    return {f.code: f.severity for f in findings}


def all_checks(packet: object) -> dict[str, Severity]:
    return codes(
        (
            *check_references(packet),  # type: ignore[arg-type]
            *check_evidence(packet, CONFIG),  # type: ignore[arg-type]
            *check_window(packet, CONFIG),  # type: ignore[arg-type]
        )
    )


class ReferenceTests(TestCase):
    def test_consistent_packets_pass(self) -> None:
        for candidate in (fx.actionable_candidate(), fx.information_candidate()):
            with self.subTest(status=candidate.status):
                self.assertEqual(all_checks(fx.packet(candidate)), {})

    def test_unloaded_candidate_is_insufficient(self) -> None:
        self.assertEqual(
            codes(check_references(replace(fx.packet(), candidate=None))),
            {"candidate_missing": Severity.MISSING},
        )

    def test_cross_case_snapshot_or_airport_references_are_rejected(self) -> None:
        candidate = fx.actionable_candidate()
        for change, code in (
            ({"case_id": "other-case"}, "cross_case_reference"),
            ({"snapshot_id": "other-snapshot"}, "snapshot_mismatch"),
        ):
            with self.subTest(code=code):
                edited = replace(candidate, envelope=replace(candidate.envelope, **change))
                found = codes(check_references(fx.packet(edited)))
                self.assertEqual(found[code], Severity.INTEGRITY)

    def test_candidate_must_be_the_one_candidate_ready_named(self) -> None:
        base = fx.packet()
        for change, code in (
            ({"candidate_id": "another-candidate"}, "candidate_reference_mismatch"),
            ({"weather_assessment_id": "another-weather"}, "weather_reference_mismatch"),
            ({"projection_id": None}, "projection_reference_mismatch"),
        ):
            with self.subTest(code=code):
                packet = replace(base, ready=replace(base.ready, **change))
                self.assertEqual(codes(check_references(packet))[code], Severity.INTEGRITY)

    def test_candidate_cannot_add_evidence_outside_the_case_snapshot(self) -> None:
        base = fx.packet()
        opening = replace(base.opening, evidence_ids=(fx.TRAFFIC_ID,))
        found = codes(check_references(replace(base, opening=opening)))
        self.assertEqual(found["evidence_outside_case"], Severity.INTEGRITY)

    def test_weather_finding_must_belong_to_the_case(self) -> None:
        for change in ({"case_id": "other"}, {"snapshot_id": "other"}, {"assessment_id": "x"}):
            with self.subTest(change=change):
                packet = fx.packet(weather=replace(fx.weather(), **change))
                self.assertIn("weather_finding_mismatch", codes(check_references(packet)))


class EvidenceTests(TestCase):
    def evidence_with(self, key: str, /, **changes: object) -> dict[str, object]:
        records: dict[str, object] = dict(fx.evidence())
        records[key] = replace(fx.evidence()[key], **changes)  # type: ignore[arg-type]
        return records

    def test_missing_evidence_is_unknown_not_zero(self) -> None:
        records = fx.evidence()
        del records[fx.DEMAND_ID]
        found = codes(check_evidence(fx.packet(evidence=records), CONFIG))
        self.assertEqual(found, {"evidence_missing": Severity.MISSING})

    def test_test_data_or_another_snapshot_cannot_support_live_advisories(self) -> None:
        for changes, code in (
            ({"data_mode": "test"}, "non_live_evidence"),
            ({"airport_icao": "KORD"}, "evidence_airport"),
            ({"snapshot_id": "older-snapshot"}, "evidence_snapshot_mismatch"),
            ({"fetched_at_utc": fx.SNAPSHOT_AT + timedelta(seconds=1)}, "evidence_after_snapshot"),
            ({"evidence_id": "renamed"}, "evidence_id_mismatch"),
        ):
            with self.subTest(code=code):
                packet = fx.packet(evidence=self.evidence_with(fx.TRAFFIC_ID, **changes))
                self.assertEqual(codes(check_evidence(packet, CONFIG))[code], Severity.INTEGRITY)

    def test_position_freshness_follows_plan_thresholds(self) -> None:
        for age, expected in (
            (timedelta(seconds=60), {}),
            (timedelta(seconds=61), {"aging_traffic": Severity.LIMITATION}),
            (timedelta(seconds=121), {"stale_traffic": Severity.MISSING}),
        ):
            with self.subTest(age=age):
                records = self.evidence_with(fx.TRAFFIC_ID, fetched_at_utc=fx.SNAPSHOT_AT - age)
                self.assertEqual(
                    codes(check_evidence(fx.packet(evidence=records), CONFIG)), expected
                )

    def test_stale_flight_records_are_insufficient(self) -> None:
        records = self.evidence_with(
            fx.DEMAND_ID, fetched_at_utc=fx.SNAPSHOT_AT - timedelta(minutes=6)
        )
        found = codes(check_evidence(fx.packet(evidence=records), CONFIG))
        self.assertEqual(found, {"stale_demand": Severity.MISSING})

    def test_aging_faa_status_is_a_limitation(self) -> None:
        records = self.evidence_with(
            fx.FAA_ID, fetched_at_utc=fx.SNAPSHOT_AT - timedelta(minutes=6)
        )
        packet = fx.packet(fx.information_candidate(), evidence=records)
        self.assertEqual(
            codes(check_evidence(packet, CONFIG)), {"aging_faa_status": Severity.LIMITATION}
        )

    def test_weather_records_need_a_recent_report_overlapping_the_window(self) -> None:
        weather_id = "test-metar-1"
        candidate = fx.information_candidate()
        candidate = replace(
            candidate,
            envelope=replace(
                candidate.envelope, evidence_ids=(*candidate.envelope.evidence_ids, weather_id)
            ),
        )
        base = fx.packet(candidate)
        opening = replace(base.opening, evidence_ids=(*fx.CASE_EVIDENCE, weather_id))
        records = dict(fx.evidence())
        records[weather_id] = fx.record(
            weather_id,
            EvidenceKind.WEATHER,
            source_time_utc=fx.SNAPSHOT_AT - timedelta(minutes=91),
            valid_until_utc=fx.SNAPSHOT_AT,
        )
        found = codes(check_evidence(replace(base, opening=opening, evidence=records), CONFIG))
        self.assertEqual(
            found,
            {"aging_weather": Severity.LIMITATION, "weather_validity_gap": Severity.LIMITATION},
        )

    def test_projections_are_never_presented_as_observations(self) -> None:
        candidate = fx.actionable_candidate()
        relabelled = TrafficMetric(
            "nearby_aircraft_total", 15, "aircraft", MetricBasis.OBSERVED, (fx.PROJECTION_ID,)
        )
        edited = replace(candidate, traffic_metrics=(*candidate.traffic_metrics, relabelled))
        found = codes(check_evidence(fx.packet(edited), CONFIG))
        self.assertEqual(found["projection_cited_as_observation"], Severity.INTEGRITY)

        invented = TrafficMetric(
            "projected_peak_runway_queue_baseline",
            3,
            "aircraft",
            MetricBasis.PROJECTED,
            (fx.TRAFFIC_ID,),
        )
        edited = replace(candidate, traffic_metrics=(invented,))
        found = codes(check_evidence(fx.packet(edited), CONFIG))
        self.assertEqual(found["projected_metric_without_projection"], Severity.INTEGRITY)

    def test_faa_and_projection_references_must_have_their_kinds(self) -> None:
        records = self.evidence_with(fx.FAA_ID, kind=EvidenceKind.TRAFFIC)
        packet = fx.packet(fx.information_candidate(), evidence=records)
        self.assertIn("faa_event_kind", codes(check_evidence(packet, CONFIG)))
        records = self.evidence_with(fx.PROJECTION_ID, kind=EvidenceKind.DEMAND)
        found = codes(check_evidence(fx.packet(evidence=records), CONFIG))
        self.assertEqual(found["projection_kind"], Severity.INTEGRITY)


class WindowTests(TestCase):
    def test_window_must_start_at_the_snapshot_and_use_a_supported_horizon(self) -> None:
        candidate = fx.actionable_candidate()
        late = replace(
            candidate,
            window_start_utc=fx.SNAPSHOT_AT + timedelta(minutes=5),
            window_end_utc=fx.SNAPSHOT_AT + timedelta(minutes=20),
        )
        self.assertIn("window_not_from_snapshot", codes(check_window(fx.packet(late), CONFIG)))
        odd = replace(candidate, window_end_utc=fx.SNAPSHOT_AT + timedelta(minutes=20))
        self.assertIn("unsupported_horizon", codes(check_window(fx.packet(odd), CONFIG)))

    def test_expired_candidates_and_elapsed_windows_are_insufficient(self) -> None:
        later = fx.packet(now_utc=fx.SNAPSHOT_AT + timedelta(minutes=16))
        self.assertEqual(
            codes(check_window(later, CONFIG)),
            {"window_elapsed": Severity.MISSING, "candidate_expired": Severity.MISSING},
        )
