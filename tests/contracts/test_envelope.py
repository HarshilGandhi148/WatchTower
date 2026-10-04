"""Test decision-relevant message invariants, without provider credentials."""

from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import TestCase

from shared.contracts import AgentName, MessageEnvelope


class MessageEnvelopeTests(TestCase):
    def setUp(self) -> None:
        created = datetime(2026, 10, 3, 16, 0, tzinfo=UTC)
        self.envelope = MessageEnvelope(
            schema_version=1,
            message_id="message-1",
            case_id="case-1",
            sender_agent=AgentName.CONGESTION,
            created_at_utc=created,
            snapshot_id="snapshot-1",
            evidence_ids=("weather-record-1", "traffic-record-1"),
            airport_icao="KDTW",
            valid_until_utc=created + timedelta(minutes=2),
        )

    def test_valid_envelope_is_immutable(self) -> None:
        with self.assertRaises(FrozenInstanceError):
            self.envelope.snapshot_id = "another-snapshot"

    def test_non_utc_or_naive_timestamps_are_rejected(self) -> None:
        for timestamp in (
            datetime(2026, 10, 3, 16),
            datetime.fromisoformat("2026-10-03T12:00:00-04:00"),
        ):
            for field in ("created_at_utc", "valid_until_utc"):
                with self.subTest(timestamp=timestamp, field=field):
                    with self.assertRaises(ValueError):
                        replace(self.envelope, **{field: timestamp})

    def test_expired_or_empty_validity_interval_is_rejected(self) -> None:
        for delta in (timedelta(0), timedelta(seconds=-1)):
            with self.subTest(delta=delta):
                with self.assertRaises(ValueError):
                    replace(
                        self.envelope,
                        valid_until_utc=self.envelope.created_at_utc + delta,
                    )

    def test_incompatible_schema_airport_and_role_are_rejected(self) -> None:
        cases: list[dict[str, Any]] = [
            {"schema_version": 2},
            {"schema_version": True},
            {"airport_icao": "KJFK"},
            {"sender_agent": "planning"},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    replace(self.envelope, **changes)

    def test_blank_ids_and_duplicate_evidence_are_rejected(self) -> None:
        cases: list[dict[str, Any]] = [
            {"message_id": " "},
            {"case_id": ""},
            {"snapshot_id": ""},
            {"evidence_ids": ("",)},
            {"evidence_ids": ("record-1", "record-1")},
            {"evidence_ids": ["record-1"]},
            {"correlation_id": ""},
            {"reply_to": ""},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    replace(self.envelope, **changes)

    def test_missing_evidence_can_be_represented_without_invented_records(self) -> None:
        self.assertEqual(replace(self.envelope, evidence_ids=()).evidence_ids, ())
