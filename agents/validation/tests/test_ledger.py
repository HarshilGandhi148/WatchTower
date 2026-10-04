"""In-memory advisory ledger: versions, suppression input, expiry, bounds and FACTS."""

from dataclasses import replace
from datetime import timedelta
from unittest import TestCase

from agents.validation import ledger as ledger_module
from agents.validation.ledger import AdvisoryLedger
from agents.validation.service import validate_packet
from shared.contracts import AdvisoryPriority, AdvisoryStatus, ValidatedAdvisory
from shared.orchestration import ValidationVerdict
from tests.fixtures import validation as fx


def advisory(**changes: object) -> ValidatedAdvisory:
    published = validate_packet(fx.packet()).advisory
    assert published is not None
    return replace(published, **changes)  # type: ignore[arg-type]


class LedgerTests(TestCase):
    def test_publish_is_idempotent_per_case_and_version(self) -> None:
        ledger = AdvisoryLedger()
        first = advisory()
        self.assertTrue(ledger.publish(first))
        self.assertFalse(ledger.publish(first))
        self.assertFalse(ledger.publish(replace(first, case_id="other-case")))  # same version
        self.assertEqual(ledger.pending(fx.NOW), (first,))

    def test_next_version_supersedes_and_feeds_previous(self) -> None:
        ledger = AdvisoryLedger()
        first = advisory()
        second = advisory(version=2, case_id="test-case-2", material_fingerprint="changed")
        ledger.publish(first)
        ledger.publish(second)
        self.assertEqual(ledger.pending(fx.NOW), (second,))
        previous = ledger.previous(first.advisory_id, fx.NOW)
        assert previous is not None
        self.assertEqual((previous.version, previous.material_fingerprint), (2, "changed"))
        self.assertIs(previous.status, AdvisoryStatus.PENDING_REVIEW)
        self.assertIsNone(ledger.previous("unknown", fx.NOW))

    def test_expired_versions_leave_the_pending_list(self) -> None:
        ledger = AdvisoryLedger()
        item = advisory()
        ledger.publish(item)
        later = item.expires_at_utc + timedelta(seconds=1)
        self.assertEqual(ledger.pending(later), ())
        previous = ledger.previous(item.advisory_id, later)
        assert previous is not None
        self.assertIs(previous.status, AdvisoryStatus.EXPIRED)

    def test_pending_is_ordered_by_priority(self) -> None:
        ledger = AdvisoryLedger()
        notice = advisory(
            advisory_id="adv-notice", case_id="c1", priority=AdvisoryPriority.INFORMATION
        )
        urgent = advisory(
            advisory_id="adv-urgent", case_id="c2", priority=AdvisoryPriority.TIME_SENSITIVE
        )
        ledger.publish(notice)
        ledger.publish(urgent)
        self.assertEqual(
            [a.advisory_id for a in ledger.pending(fx.NOW)], ["adv-urgent", "adv-notice"]
        )

    def test_internal_results_are_recorded_once_and_bounded(self) -> None:
        ledger = AdvisoryLedger()
        self.assertTrue(
            ledger.record_result("c1", ValidationVerdict.REJECT, "reject", ("no_change",), fx.NOW)
        )
        self.assertFalse(
            ledger.record_result("c1", ValidationVerdict.REJECT, "reject", ("no_change",), fx.NOW)
        )
        for n in range(ledger_module.MAX_RESULTS + 5):
            ledger.record_result(
                f"x{n}", ValidationVerdict.INSUFFICIENT_DATA, "timeout", (), fx.NOW
            )
        self.assertEqual(len(ledger._results), ledger_module.MAX_RESULTS)
        self.assertEqual(len(ledger.recent_results()), ledger_module.FACTS_RESULTS)

    def test_advisories_are_bounded(self) -> None:
        ledger = AdvisoryLedger()
        for n in range(ledger_module.MAX_ADVISORIES + 3):
            ledger.publish(advisory(advisory_id=f"adv-{n}", case_id=f"case-{n}"))
        self.assertEqual(len(ledger.pending(fx.NOW)), ledger_module.MAX_ADVISORIES)

    def test_facts_hold_validated_values_only(self) -> None:
        ledger = AdvisoryLedger()
        item = advisory()
        ledger.publish(item)
        ledger.record_result("c9", ValidationVerdict.REJECT, "reject", ("no_change",), fx.NOW)
        facts = ledger.facts(fx.NOW)
        (pending,) = facts["pending_advisories"]
        self.assertEqual(pending["title"], item.title)
        self.assertEqual(pending["evidence_references"], len(item.evidence_ids))
        self.assertEqual(
            pending["action_parameters"][0],
            {"name": "release_cap", "value": 30, "unit": "aircraft/hour"},
        )
        self.assertEqual(facts["recent_unpublished_results"][0]["finding_codes"], ["no_change"])
        self.assertIn("never approves", facts["note"])
