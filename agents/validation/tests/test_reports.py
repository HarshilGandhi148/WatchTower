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

    def test_failed_feed_remains_unknown(self) -> None:
        report = self.evaluate_capture("outage")
        self.assertEqual(report.outcome, "insufficient_data")
        self.assertEqual(next(c.status for c in report.checks if c.name == "inputs"), "fail")

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
        self.assertEqual(
            evaluate(candidate, c["snapshot"], finding, 1, NOW + timedelta(minutes=10)).outcome,
            "insufficient_data",
        )


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
