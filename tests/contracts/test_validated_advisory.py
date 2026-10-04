"""ValidatedAdvisory structural rules: pending review only, provenance, bounded text."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import TestCase

from shared.contracts import (
    MAX_ADVISORY_TEXT,
    ActionParameter,
    ActionType,
    AdvisoryPriority,
    AdvisoryStatus,
    AdvisoryType,
    EvidenceQuality,
    ValidatedAdvisory,
)

NOW = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)


def advisory() -> ValidatedAdvisory:
    return ValidatedAdvisory(
        advisory_id="adv-1",
        version=1,
        case_id="case-1",
        snapshot_id="snap-1",
        advisory_type=AdvisoryType.DEPARTURE_RELEASE_REVIEW,
        title="Review departure release rate",
        suggested_response="Review an aggregate release cap of 30 aircraft/hour.",
        reason="Projected peak runway queue 7 aircraft without a response.",
        priority=AdvisoryPriority.ATTENTION,
        owner_role="Departure flow coordinator",
        affected_resource="KDTW departures",
        window_start_utc=NOW,
        window_end_utc=NOW + timedelta(minutes=15),
        evidence_ids=("flights-1", "proj-1"),
        quality=EvidenceQuality.LIMITED,
        material_fingerprint="abc123",
        created_at_utc=NOW,
        expires_at_utc=NOW + timedelta(minutes=2),
        projection_id="proj-1",
        action_type=ActionType.REVIEW_RELEASE_RATE,
        action_parameters=(ActionParameter("release_cap", 30, "aircraft/hour"),),
        assumptions=("capacity is operator configured",),
    )


class ValidatedAdvisoryTests(TestCase):
    def test_valid_advisory_is_pending_and_immutable(self) -> None:
        item = advisory()
        self.assertIs(item.status, AdvisoryStatus.PENDING_REVIEW)
        with self.assertRaises(FrozenInstanceError):
            item.title = "changed"  # type: ignore[misc]

    def test_validator_cannot_publish_a_decided_status(self) -> None:
        for status in AdvisoryStatus:
            if status is AdvisoryStatus.PENDING_REVIEW:
                continue
            with self.subTest(status=status), self.assertRaises(ValueError):
                replace(advisory(), status=status)

    def test_times_must_be_utc_and_ordered(self) -> None:
        cases: list[dict[str, Any]] = [
            {"created_at_utc": datetime(2026, 10, 4, 16)},
            {"window_start_utc": datetime.fromisoformat("2026-10-04T12:00:00-04:00")},
            {"window_end_utc": NOW},
            {"expires_at_utc": NOW},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(advisory(), **changes)

    def test_evidence_is_required_and_includes_the_projection(self) -> None:
        cases: list[dict[str, Any]] = [
            {"evidence_ids": ()},
            {"evidence_ids": ("proj-1", "proj-1")},
            {"evidence_ids": ["flights-1", "proj-1"]},
            {"evidence_ids": ("flights-1",)},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(advisory(), **changes)

    def test_unavailable_quality_is_not_published(self) -> None:
        with self.assertRaises(ValueError):
            replace(advisory(), quality=EvidenceQuality.UNAVAILABLE)

    def test_parameters_only_with_a_flow_review_action(self) -> None:
        for action in (None, ActionType.INFORMATION_NOTICE):
            with self.subTest(action=action), self.assertRaises(ValueError):
                replace(advisory(), action_type=action)
        doubled = (
            ActionParameter("release_cap", 30, "aircraft/hour"),
            ActionParameter("release_cap", 20, "aircraft/hour"),
        )
        with self.assertRaises(ValueError):
            replace(advisory(), action_parameters=doubled)

    def test_text_is_bounded_and_printable(self) -> None:
        cases: list[dict[str, Any]] = [
            {"title": ""},
            {"title": "x" * (MAX_ADVISORY_TEXT + 1)},
            {"reason": "line\nbreak"},
            {"assumptions": ("ok", "\x1b[31m")},
            {"owner_role": "   "},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(advisory(), **changes)

    def test_identity_version_and_airport(self) -> None:
        cases: list[dict[str, Any]] = [
            {"version": 0},
            {"version": True},
            {"advisory_id": ""},
            {"material_fingerprint": ""},
            {"airport_icao": "KORD"},
            {"priority": "attention"},
        ]
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                replace(advisory(), **changes)
