"""Advisory identity, versioning, expiry, priority and routing (TEST data)."""

from dataclasses import replace
from datetime import timedelta
from unittest import TestCase

from agents.validation import lifecycle
from agents.validation.lifecycle import PreviousVersion, VersionDecision
from agents.validation.packet import ValidationConfig
from shared.contracts import (
    ActionParameter,
    ActionType,
    AdvisoryPriority,
    AdvisoryStatus,
    AdvisoryType,
    EvidenceQuality,
    FlowCandidate,
)
from tests.fixtures import validation as fx

CONFIG = ValidationConfig()


def fingerprint(
    candidate: FlowCandidate, quality: EvidenceQuality = EvidenceQuality.ADEQUATE
) -> str:
    return lifecycle.material_fingerprint(
        candidate, ActionType.REVIEW_RELEASE_RATE, quality, fx.projection()
    )


def previous(status: AdvisoryStatus, *, expired: bool = False, fp: str = "fp") -> PreviousVersion:
    return PreviousVersion(
        advisory_id="adv-1",
        version=3,
        material_fingerprint=fp,
        status=status,
        expires_at_utc=fx.NOW + (timedelta(seconds=-1) if expired else timedelta(seconds=30)),
    )


def shifted(candidate: FlowCandidate, minutes: int) -> FlowCandidate:
    delta = timedelta(minutes=minutes)
    return replace(
        candidate,
        window_start_utc=candidate.window_start_utc + delta,
        window_end_utc=candidate.window_end_utc + delta,
    )


class IdentityTests(TestCase):
    def test_same_issue_resource_and_window_bucket_share_an_id(self) -> None:
        candidate = fx.actionable_candidate()
        self.assertEqual(
            lifecycle.advisory_id(candidate), lifecycle.advisory_id(shifted(candidate, 1))
        )
        self.assertNotEqual(
            lifecycle.advisory_id(candidate), lifecycle.advisory_id(shifted(candidate, 15))
        )
        other_issue = replace(candidate, issue_type=AdvisoryType.WEATHER_FLOW_REVIEW)
        self.assertNotEqual(lifecycle.advisory_id(candidate), lifecycle.advisory_id(other_issue))

    def test_minor_refreshes_are_not_material(self) -> None:
        candidate = fx.actionable_candidate()
        nearby = replace(candidate.traffic_metrics[0], value=44)
        refreshed = replace(
            candidate,
            envelope=replace(candidate.envelope, message_id="test-candidate-2"),
            traffic_metrics=(nearby, *candidate.traffic_metrics[1:]),
        )
        self.assertEqual(fingerprint(candidate), fingerprint(refreshed))

    def test_parameters_quality_and_projected_values_are_material(self) -> None:
        candidate = fx.actionable_candidate()
        cap = replace(
            candidate,
            proposed_action_parameters=(
                ActionParameter("release_cap", 24, "aircraft/hour"),
                *candidate.proposed_action_parameters[1:],
            ),
        )
        self.assertNotEqual(fingerprint(candidate), fingerprint(cap))
        self.assertNotEqual(fingerprint(candidate), fingerprint(candidate, EvidenceQuality.LIMITED))
        other = fx.projection(release_cap=24)
        self.assertNotEqual(
            fingerprint(candidate),
            lifecycle.material_fingerprint(
                candidate, ActionType.REVIEW_RELEASE_RATE, EvidenceQuality.ADEQUATE, other
            ),
        )


class VersionTests(TestCase):
    def test_first_version(self) -> None:
        self.assertEqual(
            lifecycle.next_version(None, "fp", fx.NOW),
            lifecycle.VersionPlan(VersionDecision.NEW, 1),
        )

    def test_unchanged_pending_approved_or_ignored_is_suppressed(self) -> None:
        for status in (
            AdvisoryStatus.PENDING_REVIEW,
            AdvisoryStatus.APPROVED,
            AdvisoryStatus.IGNORED,
        ):
            with self.subTest(status=status):
                plan = lifecycle.next_version(previous(status), "fp", fx.NOW)
                self.assertEqual(plan, lifecycle.VersionPlan(VersionDecision.DUPLICATE, 3))

    def test_material_change_or_lapsed_version_creates_the_next_version(self) -> None:
        for prior, fp in (
            (previous(AdvisoryStatus.PENDING_REVIEW), "changed"),
            (previous(AdvisoryStatus.APPROVED), "changed"),
            (previous(AdvisoryStatus.PENDING_REVIEW, expired=True), "fp"),
            (previous(AdvisoryStatus.EXPIRED), "fp"),
            (previous(AdvisoryStatus.SUPERSEDED), "fp"),
        ):
            with self.subTest(status=prior.status, fp=fp):
                plan = lifecycle.next_version(prior, fp, fx.NOW)
                self.assertEqual(plan, lifecycle.VersionPlan(VersionDecision.SUPERSEDE, 4))


class ExpiryPriorityRoutingTests(TestCase):
    def test_expiry_uses_the_lifetime_but_never_outlasts_the_window(self) -> None:
        candidate = fx.actionable_candidate()
        self.assertEqual(
            lifecycle.expires_at(candidate, fx.NOW, CONFIG), fx.NOW + timedelta(seconds=120)
        )
        late = candidate.window_end_utc - timedelta(seconds=30)
        self.assertEqual(lifecycle.expires_at(candidate, late, CONFIG), candidate.window_end_utc)

    def test_priority(self) -> None:
        packet = fx.projection()
        candidate = fx.actionable_candidate(packet)
        self.assertIs(
            lifecycle.priority(fx.information_candidate(), None, CONFIG),
            AdvisoryPriority.INFORMATION,
        )
        self.assertIs(lifecycle.priority(candidate, packet, CONFIG), AdvisoryPriority.ATTENTION)
        urgent = replace(CONFIG, time_sensitive_peak_minute=15)
        self.assertIs(
            lifecycle.priority(candidate, packet, urgent), AdvisoryPriority.TIME_SENSITIVE
        )

    def test_every_issue_routes_to_a_demo_role(self) -> None:
        for issue in AdvisoryType:
            with self.subTest(issue=issue):
                self.assertTrue(lifecycle.owner_role(issue, CONFIG).strip())
        with self.assertRaises(ValueError):
            ValidationConfig(owner_roles={AdvisoryType.WEATHER_FLOW_REVIEW: "Flow coordinator"})
