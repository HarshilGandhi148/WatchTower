"""WeatherAssessmentRequest structural rules: sender role, UTC window, bounded attempt."""

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest import TestCase

from shared.contracts import AgentName, MessageEnvelope, WeatherAssessmentRequest

NOW = datetime(2026, 10, 4, 1, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=15)


def envelope(sender: AgentName = AgentName.CONGESTION) -> MessageEnvelope:
    return MessageEnvelope(
        schema_version=1,
        message_id="case-1:weather-request:1",
        case_id="case-1",
        sender_agent=sender,
        created_at_utc=NOW,
        snapshot_id="snap-1",
        evidence_ids=("adsb-1",),
        airport_icao="KDTW",
        valid_until_utc=NOW + timedelta(minutes=2),
    )


class AssessmentRequestTests(TestCase):
    def test_valid_request_defaults_to_first_attempt(self) -> None:
        request = WeatherAssessmentRequest(envelope(), NOW, LATER)
        self.assertEqual(request.attempt, 1)

    def test_second_attempt_is_the_last_allowed(self) -> None:
        self.assertEqual(WeatherAssessmentRequest(envelope(), NOW, LATER, attempt=2).attempt, 2)
        with self.assertRaises(ValueError):
            WeatherAssessmentRequest(envelope(), NOW, LATER, attempt=3)

    def test_only_congestion_may_send_it(self) -> None:
        for sender in (AgentName.WEATHER, AgentName.VALIDATION):
            with self.subTest(sender=sender), self.assertRaises(ValueError):
                WeatherAssessmentRequest(envelope(sender), NOW, LATER)

    def test_window_must_be_utc_and_forward(self) -> None:
        cases: list[dict[str, Any]] = [
            {"window_start_utc": datetime(2026, 10, 4, 1)},
            {"window_end_utc": datetime.fromisoformat("2026-10-03T21:15:00-04:00")},
            {"window_end_utc": NOW},
        ]
        for changes in cases:
            kwargs: dict[str, Any] = {
                "envelope": envelope(),
                "window_start_utc": NOW,
                "window_end_utc": LATER,
                **changes,
            }
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                WeatherAssessmentRequest(**kwargs)

    def test_attempt_must_be_a_real_integer(self) -> None:
        for attempt in (0, -1, True, 1.0):
            with self.subTest(attempt=attempt), self.assertRaises(ValueError):
                WeatherAssessmentRequest(envelope(), NOW, LATER, attempt=attempt)  # type: ignore[arg-type]

    def test_envelope_must_be_an_envelope(self) -> None:
        with self.assertRaises(ValueError):
            WeatherAssessmentRequest("not-an-envelope", NOW, LATER)  # type: ignore[arg-type]
