"""Case coordination invariants; these do not prove database/provider integration."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from unittest import TestCase

from agents.validation.orchestrator import Dispatch, OrchestrationError, Orchestrator
from shared.contracts import AgentName
from shared.orchestration import CasePhase, ValidationVerdict
from tests.fixtures.orchestration import TEST_ADDRESSES, candidate, opening


class OrchestratorTests(TestCase):
    def setUp(self) -> None:
        self.now = datetime(2026, 10, 3, 16, tzinfo=UTC)
        self.opening = opening(self.now)
        self.coordinator = Orchestrator(TEST_ADDRESSES)

    def start(self) -> Dispatch:
        dispatch = self.coordinator.open_case(
            TEST_ADDRESSES[AgentName.WEATHER], self.opening, now=self.now
        )
        self.assertIsNotNone(dispatch)
        assert dispatch is not None
        return dispatch

    def test_exactly_three_distinct_identities_are_required(self) -> None:
        for addresses in (
            {AgentName.WEATHER: "one"},
            {role: "same" for role in AgentName},
            {**TEST_ADDRESSES, AgentName.WEATHER: ""},
        ):
            with self.subTest(addresses=addresses), self.assertRaises(OrchestrationError):
                Orchestrator(addresses)

    def test_either_specialist_can_open_a_case(self) -> None:
        for role in (AgentName.WEATHER, AgentName.CONGESTION):
            with self.subTest(role=role):
                coordinator = Orchestrator(TEST_ADDRESSES)
                dispatch = coordinator.open_case(
                    TEST_ADDRESSES[role],
                    replace(self.opening, sender_agent=role),
                    now=self.now,
                )
                assert dispatch is not None
                self.assertEqual(dispatch.recipient, TEST_ADDRESSES[AgentName.CONGESTION])
                self.assertEqual(dispatch.request.envelope.snapshot_id, self.opening.snapshot_id)
                self.assertEqual(dispatch.request.envelope.sender_agent, AgentName.VALIDATION)

    def test_declared_sender_cannot_impersonate_another_identity(self) -> None:
        for sender in ("unknown", TEST_ADDRESSES[AgentName.CONGESTION]):
            with self.subTest(sender=sender), self.assertRaises(OrchestrationError):
                self.coordinator.open_case(sender, self.opening, now=self.now)

    def test_validator_cannot_trigger_a_specialist_case(self) -> None:
        with self.assertRaises(OrchestrationError):
            self.coordinator.open_case(
                TEST_ADDRESSES[AgentName.VALIDATION],
                replace(self.opening, sender_agent=AgentName.VALIDATION),
                now=self.now,
            )

    def test_duplicate_and_simultaneous_specialist_detections_do_not_redispatch(self) -> None:
        self.start()
        retry = replace(
            self.opening, sender_agent=AgentName.CONGESTION, message_id="test-other-detection"
        )
        self.assertIsNone(
            self.coordinator.open_case(TEST_ADDRESSES[AgentName.CONGESTION], retry, now=self.now)
        )

    def test_same_case_cannot_switch_snapshot_or_evidence(self) -> None:
        self.start()
        for changed in (
            replace(self.opening, snapshot_id="test-other-snapshot"),
            replace(self.opening, evidence_ids=("test-other-evidence",)),
        ):
            with self.subTest(changed=changed), self.assertRaises(OrchestrationError):
                self.coordinator.open_case(TEST_ADDRESSES[AgentName.WEATHER], changed, now=self.now)

    def test_expired_future_or_non_utc_messages_are_rejected(self) -> None:
        for now in (
            self.now - timedelta(seconds=1),
            self.opening.valid_until_utc,
            self.now.replace(tzinfo=None),
        ):
            with self.subTest(now=now), self.assertRaises(OrchestrationError):
                self.coordinator.open_case(TEST_ADDRESSES[AgentName.WEATHER], self.opening, now=now)

    def test_candidate_needs_current_request_snapshot_round_and_correlation(self) -> None:
        original = candidate(self.start())
        for changed in (
            replace(original, assessment_round=2),
            replace(original, envelope=replace(original.envelope, reply_to="test-wrong-request")),
            replace(
                original, envelope=replace(original.envelope, correlation_id="test-wrong-case")
            ),
            replace(
                original, envelope=replace(original.envelope, snapshot_id="test-wrong-snapshot")
            ),
            replace(
                original,
                envelope=replace(
                    original.envelope,
                    valid_until_utc=original.envelope.valid_until_utc + timedelta(seconds=1),
                ),
            ),
        ):
            with self.subTest(changed=changed), self.assertRaises(OrchestrationError):
                self.coordinator.receive_candidate(
                    TEST_ADDRESSES[AgentName.CONGESTION], changed, now=self.now
                )

    def test_wrong_identity_cannot_submit_a_candidate(self) -> None:
        packet = candidate(self.start())
        with self.assertRaises(OrchestrationError):
            self.coordinator.receive_candidate(
                TEST_ADDRESSES[AgentName.WEATHER], packet, now=self.now
            )

    def test_duplicate_candidate_is_idempotent_and_changed_retry_is_rejected(self) -> None:
        packet = candidate(self.start())
        self.coordinator.receive_candidate(
            TEST_ADDRESSES[AgentName.CONGESTION], packet, now=self.now
        )
        self.assertIsNone(
            self.coordinator.receive_candidate(
                TEST_ADDRESSES[AgentName.CONGESTION], packet, now=self.now
            )
        )
        with self.assertRaises(OrchestrationError):
            self.coordinator.receive_candidate(
                TEST_ADDRESSES[AgentName.CONGESTION],
                replace(packet, candidate_id="test-changed-candidate"),
                now=self.now,
            )

    def test_one_correction_is_allowed_without_extending_deadline(self) -> None:
        first = candidate(self.start())
        self.coordinator.receive_candidate(
            TEST_ADDRESSES[AgentName.CONGESTION], first, now=self.now
        )
        correction = self.coordinator.finish_validation(
            first, ValidationVerdict.REASSESS, now=self.now + timedelta(seconds=1)
        )
        assert correction is not None
        self.assertEqual(correction.request.assessment_round, 2)
        self.assertEqual(
            correction.request.envelope.valid_until_utc, first.envelope.valid_until_utc
        )
        self.assertEqual(correction.request.envelope.correlation_id, first.envelope.correlation_id)
        second = candidate(correction)
        self.coordinator.receive_candidate(
            TEST_ADDRESSES[AgentName.CONGESTION], second, now=self.now + timedelta(seconds=2)
        )
        self.assertIsNone(
            self.coordinator.finish_validation(
                second, ValidationVerdict.REASSESS, now=self.now + timedelta(seconds=3)
            )
        )
        self.assertEqual(
            self.coordinator.get_case(self.opening.case_id).phase, CasePhase.INSUFFICIENT_DATA
        )
        self.assertEqual(
            self.coordinator.get_case(self.opening.case_id).reason, "reassessment_limit"
        )

    def test_late_result_cannot_complete_a_corrected_case(self) -> None:
        first = candidate(self.start())
        self.coordinator.receive_candidate(
            TEST_ADDRESSES[AgentName.CONGESTION], first, now=self.now
        )
        self.coordinator.finish_validation(first, ValidationVerdict.REASSESS, now=self.now)
        with self.assertRaises(OrchestrationError):
            self.coordinator.finish_validation(first, ValidationVerdict.PUBLISH, now=self.now)

    def test_publish_means_ready_for_human_review_never_approved(self) -> None:
        packet = candidate(self.start())
        self.coordinator.receive_candidate(
            TEST_ADDRESSES[AgentName.CONGESTION], packet, now=self.now
        )
        self.coordinator.finish_validation(packet, ValidationVerdict.PUBLISH, now=self.now)
        self.assertEqual(
            self.coordinator.get_case(self.opening.case_id).phase, CasePhase.READY_TO_PUBLISH
        )
        self.assertEqual(len(self.coordinator.pending_notifications()), 1)

    def test_validation_reject_and_insufficient_data_remain_internal_outcomes(self) -> None:
        for verdict, expected in (
            (ValidationVerdict.REJECT, CasePhase.REJECTED),
            (ValidationVerdict.INSUFFICIENT_DATA, CasePhase.INSUFFICIENT_DATA),
        ):
            with self.subTest(verdict=verdict):
                self.setUp()
                packet = candidate(self.start())
                self.coordinator.receive_candidate(
                    TEST_ADDRESSES[AgentName.CONGESTION], packet, now=self.now
                )
                self.coordinator.finish_validation(packet, verdict, now=self.now)
                self.assertEqual(self.coordinator.get_case(self.opening.case_id).phase, expected)

    def test_timeout_and_expired_candidate_cannot_be_published(self) -> None:
        packet = candidate(self.start())
        shorter = replace(
            packet,
            envelope=replace(packet.envelope, valid_until_utc=self.now + timedelta(seconds=10)),
        )
        self.coordinator.receive_candidate(
            TEST_ADDRESSES[AgentName.CONGESTION], shorter, now=self.now
        )
        with self.assertRaises(OrchestrationError):
            self.coordinator.finish_validation(
                shorter, ValidationVerdict.PUBLISH, now=self.now + timedelta(seconds=10)
            )
        self.assertEqual(len(self.coordinator.expire(now=self.now + timedelta(seconds=10))), 1)
        self.assertEqual(self.coordinator.expire(now=self.now + timedelta(seconds=11)), ())

    def test_unpersisted_publish_outcome_expires_before_retry(self) -> None:
        packet = candidate(self.start())
        self.coordinator.receive_candidate(
            TEST_ADDRESSES[AgentName.CONGESTION], packet, now=self.now
        )
        self.coordinator.finish_validation(packet, ValidationVerdict.PUBLISH, now=self.now)
        self.coordinator.expire(now=self.now + timedelta(seconds=60))
        self.assertEqual(
            self.coordinator.pending_notifications()[0].phase, CasePhase.INSUFFICIENT_DATA
        )

    def test_state_is_bounded_and_pruned_only_after_persistence_and_original_expiry(self) -> None:
        self.coordinator = Orchestrator(TEST_ADDRESSES, max_cases=1)
        self.start()
        other = replace(self.opening, case_id="test-other-case")
        with self.assertRaises(OrchestrationError):
            self.coordinator.open_case(TEST_ADDRESSES[AgentName.WEATHER], other, now=self.now)
        self.coordinator.expire(now=self.now + timedelta(seconds=60))
        self.coordinator.prune(now=self.opening.valid_until_utc)
        self.assertEqual(len(self.coordinator.pending_notifications()), 1)
        self.coordinator.mark_notified(self.opening.case_id)
        self.coordinator.prune(now=self.opening.valid_until_utc)
        with self.assertRaises(OrchestrationError):
            self.coordinator.get_case(self.opening.case_id)

    def test_out_of_catalog_rounds_and_missing_references_are_rejected(self) -> None:
        packet = candidate(self.start())
        for changes in (
            {"assessment_round": 3},
            {"assessment_round": True},
            {"candidate_id": ""},
            {"weather_assessment_id": ""},
            {"projection_id": ""},
        ):
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(packet, **changes)
