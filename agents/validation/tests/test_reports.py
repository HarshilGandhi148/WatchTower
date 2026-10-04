"""Real evidence checking; missing inputs and failures cannot become approval."""

import json
from dataclasses import replace
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import AsyncMock, MagicMock

from agents.validation.reports import ReportValidator, evaluate
from agents.validation.storage import ValidationStorage
from shared.persistence import candidate_from_dict
from shared.spacetimedb_adapter import SpacetimeAdapter, SpacetimeError
from tests.fixtures.replay.generate import NOW, capture


class ReportTests(TestCase):
    def evaluate_capture(self, name="concern", **changes):  # type: ignore[no-untyped-def]
        c = capture(name)
        candidate = candidate_from_dict(json.loads(c["rows"]["candidates"][-1]["contentJson"]))
        finding = json.loads(c["rows"]["findings"][-1]["contentJson"])
        return evaluate(
            replace(candidate, **changes), c["snapshot"], finding, 1, NOW + timedelta(seconds=8)
        )

    def test_real_metrics_support_only_informational_review(self) -> None:
        report = self.evaluate_capture()
        self.assertEqual(report.outcome, "supported")
        self.assertEqual(len(report.checks), 6)
        self.assertIn("no operational", report.checks[4].reason)
        self.assertIn("nearby aircraft within", report.suggestion)
        self.assertIn("KDTW", report.suggestion)

    def test_incomplete_report_names_weather_gaps_and_uses_current_counts(self) -> None:
        c = capture("concern")
        candidate = candidate_from_dict(json.loads(c["rows"]["candidates"][0]["contentJson"]))
        finding = json.loads(c["rows"]["findings"][0]["contentJson"])
        finding.update(status="insufficient_data", missing_inputs=["TAF_window_coverage"])
        report = evaluate(candidate, c["snapshot"], finding, 1, NOW + timedelta(seconds=8))
        self.assertEqual(report.outcome, "insufficient_data")
        self.assertIn("TAF coverage for the analysis window", report.suggestion)
        self.assertIn("TAF_window_coverage", report.limitations)
        self.assertIn("nearby aircraft within", report.suggestion)
        self.assertTrue(report.suggestion.startswith("Review the available KDTW weather"))
        self.assertNotIn("Assessment incomplete", report.suggestion)
        self.assertNotIn("Obtain the missing or verifiable inputs", report.suggestion)
        self.assertNotEqual(report.suggestion, self.evaluate_capture("outage").suggestion)

    def test_failed_feed_remains_unknown(self) -> None:
        report = self.evaluate_capture("outage")
        self.assertEqual(report.outcome, "insufficient_data")
        self.assertEqual(next(c.status for c in report.checks if c.name == "inputs"), "fail")
        self.assertTrue(report.suggestion.startswith("Review weather-related flow pressure"))
        self.assertIn("Nearby traffic is unknown", report.suggestion)

    def test_both_missing_feeds_still_give_a_recovery_recommendation(self) -> None:
        c = capture("outage")
        candidate = candidate_from_dict(json.loads(c["rows"]["candidates"][0]["contentJson"]))
        finding = json.loads(c["rows"]["findings"][0]["contentJson"])
        c["snapshot"]["batches"] = []
        c["snapshot"]["weather"] = []
        finding.update(status="insufficient_data", missing_inputs=["fresh_METAR"])
        report = evaluate(candidate, c["snapshot"], finding, 1, NOW + timedelta(seconds=8))
        self.assertEqual(report.outcome, "insufficient_data")
        self.assertTrue(report.suggestion.startswith("Refresh KDTW weather and aircraft updates"))
        self.assertIn("Nearby traffic is unknown", report.suggestion)
        self.assertIn("weather is unavailable", report.suggestion)

    def test_missing_weather_still_recommends_monitoring_observed_aircraft(self) -> None:
        c = capture("concern")
        candidate = candidate_from_dict(json.loads(c["rows"]["candidates"][0]["contentJson"]))
        finding = json.loads(c["rows"]["findings"][0]["contentJson"])
        c["snapshot"]["weather"] = []
        finding.update(status="insufficient_data", missing_inputs=["fresh_METAR"])
        report = evaluate(candidate, c["snapshot"], finding, 1, NOW + timedelta(seconds=8))
        self.assertEqual(report.outcome, "insufficient_data")
        self.assertTrue(
            report.suggestion.startswith("Monitor nearby aircraft and refresh KDTW weather")
        )
        self.assertIn("nearby aircraft within", report.suggestion)
        self.assertNotIn("no material weather impact", report.suggestion)

    def test_missing_traffic_still_recommends_weather_review(self) -> None:
        c = capture("concern")
        candidate = candidate_from_dict(json.loads(c["rows"]["candidates"][0]["contentJson"]))
        finding = json.loads(c["rows"]["findings"][0]["contentJson"])
        c["snapshot"]["batches"] = []
        report = evaluate(
            replace(candidate, traffic_metrics=()),
            c["snapshot"],
            finding,
            1,
            NOW + timedelta(seconds=8),
        )
        self.assertEqual(report.outcome, "insufficient_data")
        self.assertTrue(report.suggestion.startswith("Review weather-related flow pressure"))
        self.assertIn("restore aircraft updates", report.suggestion)
        self.assertIn("Nearby traffic is unknown", report.suggestion)

    def test_unverifiable_optional_metric_does_not_block_available_weather_recommendation(
        self,
    ) -> None:
        c = capture("concern")
        candidate = candidate_from_dict(json.loads(c["rows"]["candidates"][0]["contentJson"]))
        unverified = replace(candidate.traffic_metrics[0], name="nearby_aircraft_change")
        report = self.evaluate_capture(traffic_metrics=(*candidate.traffic_metrics, unverified))
        self.assertEqual(report.outcome, "insufficient_data")
        self.assertEqual(next(c.status for c in report.checks if c.name == "metrics"), "fail")
        self.assertTrue(report.suggestion.startswith("Review weather-related flow pressure"))
        self.assertIn(
            "Supplemental traffic metrics not used: nearby_aircraft_change", report.limitations
        )
        self.assertNotIn("cannot be verified", report.suggestion)

    def test_missing_metrics_still_uses_counts_recomputed_from_evidence(self) -> None:
        report = self.evaluate_capture(traffic_metrics=())
        self.assertEqual(report.outcome, "supported")
        self.assertTrue(report.suggestion.startswith("Review weather-related flow pressure"))
        self.assertIn("nearby aircraft within", report.suggestion)

    def test_evidence_captured_after_window_start_remains_verifiable(self) -> None:
        c = capture("concern")
        candidate = candidate_from_dict(json.loads(c["rows"]["candidates"][0]["contentJson"]))
        finding = json.loads(c["rows"]["findings"][0]["contentJson"])
        source = c["snapshot"]["batches"][0]["payload"]["snapshot"]
        after_start = (NOW + timedelta(seconds=1)).isoformat()
        source["fetched_at_utc"] = after_start
        for observation in source["observations"]:
            observation["observed_at_utc"] = after_start
        c["snapshot"]["cutoff_at_utc"] = (NOW + timedelta(seconds=2)).isoformat()
        report = evaluate(candidate, c["snapshot"], finding, 1, NOW + timedelta(seconds=8))
        self.assertEqual(report.outcome, "supported")
        c["snapshot"]["cutoff_at_utc"] = NOW.isoformat()
        report = evaluate(candidate, c["snapshot"], finding, 1, NOW + timedelta(seconds=8))
        self.assertEqual(report.outcome, "insufficient_data")

    def test_correction_keeps_snapshot_and_rejects_invented_count(self) -> None:
        rows = capture("correction")["rows"]["reports"]
        first, second = (json.loads(r["contentJson"]) for r in rows)
        self.assertEqual(first["outcome"], "rejected")
        self.assertEqual(second["outcome"], "supported")
        self.assertEqual(first["snapshot_id"], second["snapshot_id"])
        self.assertNotEqual(first["candidate_id"], second["candidate_id"])

    def test_wrong_unit_unknown_metric_and_expiry_fail(self) -> None:
        c = capture("concern")
        candidate = candidate_from_dict(json.loads(c["rows"]["candidates"][0]["contentJson"]))
        for metric, outcome in (
            (replace(candidate.traffic_metrics[0], unit="meters"), "rejected"),
            (replace(candidate.traffic_metrics[0], name="invented"), "insufficient_data"),
        ):
            with self.subTest(outcome=outcome):
                self.assertEqual(self.evaluate_capture(traffic_metrics=(metric,)).outcome, outcome)
        finding = json.loads(c["rows"]["findings"][0]["contentJson"])
        expired = evaluate(candidate, c["snapshot"], finding, 1, NOW + timedelta(minutes=10))
        self.assertEqual(expired.outcome, "insufficient_data")
        self.assertTrue(expired.suggestion.startswith("Reassess this candidate"))
        self.assertNotIn("..", expired.suggestion)


class PersistenceTests(IsolatedAsyncioTestCase):
    async def test_save_failure_exposes_no_report_and_retry_reuses_stored_result(self) -> None:
        from shared.persistence import ready_from_dict

        c = capture("concern")
        rows = c["rows"]
        adapter = MagicMock(spec=SpacetimeAdapter)
        adapter.read_records = AsyncMock(return_value=[])
        adapter.load_candidate = AsyncMock(
            return_value=candidate_from_dict(json.loads(rows["candidates"][0]["contentJson"]))
        )
        adapter.load_snapshot = AsyncMock(return_value=c["snapshot"])
        adapter.load_record = AsyncMock(
            return_value={"content_json": rows["findings"][0]["contentJson"]}
        )
        adapter.call = AsyncMock(side_effect=SpacetimeError("TEST failure"))
        storage = MagicMock(spec=ValidationStorage)
        storage.adapter = adapter
        storage.retrieve = AsyncMock()
        ready = ready_from_dict(json.loads(rows["deliveries"][0]["contentJson"]))
        validator = ReportValidator(storage)
        with self.assertRaises(SpacetimeError):
            await validator.report(ready, NOW + timedelta(seconds=8))
        adapter.call.side_effect = None
        result = await validator.report(ready, NOW + timedelta(seconds=8))
        adapter.read_records.return_value = [{"content_json": json.dumps(result)}]
        self.assertEqual(await validator.report(ready, NOW + timedelta(seconds=9)), result)
        self.assertEqual(adapter.call.await_count, 2)
