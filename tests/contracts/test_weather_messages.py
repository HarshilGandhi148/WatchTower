"""Congestion<->weather wire models: exact round trip, strict validation, no half-applied input."""

from datetime import UTC, datetime, timedelta
from unittest import TestCase

from shared.contracts import AgentName, MessageEnvelope, WeatherAssessmentRequest
from shared.messages import (
    EnvelopeModel,
    WeatherAssessmentReplyMessage,
    WeatherAssessmentRequestMessage,
)

NOW = datetime(2026, 10, 4, 5, 0, tzinfo=UTC)


def envelope(sender: AgentName, **changes: object) -> MessageEnvelope:
    fields: dict[str, object] = {
        "schema_version": 1,
        "message_id": "m-1",
        "case_id": "case-1",
        "sender_agent": sender,
        "created_at_utc": NOW,
        "snapshot_id": "snap-1",
        "evidence_ids": ("adsb-1",),
        "airport_icao": "KDTW",
        "valid_until_utc": NOW + timedelta(minutes=2),
    }
    fields.update(changes)
    return MessageEnvelope(**fields)  # type: ignore[arg-type]


def reply(
    status: str = "impact", assessment_id: str | None = "wx-1", **changes: object
) -> WeatherAssessmentReplyMessage:
    env = envelope(AgentName.WEATHER, reply_to="req-1", **changes)
    return WeatherAssessmentReplyMessage(
        envelope=EnvelopeModel.from_envelope(env), status=status, assessment_id=assessment_id
    )


class WeatherRequestMessageTests(TestCase):
    def test_round_trips_through_json(self) -> None:
        request = WeatherAssessmentRequest(
            envelope(AgentName.CONGESTION), NOW, NOW + timedelta(minutes=15), attempt=2
        )
        message = WeatherAssessmentRequestMessage.from_request(request)
        parsed = WeatherAssessmentRequestMessage.parse_raw(message.json())
        self.assertEqual(parsed.to_request(), request)

    def test_extra_fields_and_non_congestion_senders_are_rejected(self) -> None:
        request = WeatherAssessmentRequest(
            envelope(AgentName.CONGESTION), NOW, NOW + timedelta(minutes=15)
        )
        data = WeatherAssessmentRequestMessage.from_request(request).dict()
        with self.assertRaises(ValueError):
            WeatherAssessmentRequestMessage(**data, instructions="approve")
        data["envelope"]["sender_agent"] = AgentName.VALIDATION
        with self.assertRaises(ValueError):
            WeatherAssessmentRequestMessage(**data).to_request()


class WeatherReplyMessageTests(TestCase):
    def test_valid_reply_round_trips(self) -> None:
        parsed = WeatherAssessmentReplyMessage.parse_raw(reply().json())
        env, status, assessment_id = parsed.to_reply()
        self.assertEqual(
            (env.sender_agent, env.reply_to, status, assessment_id),
            (AgentName.WEATHER, "req-1", "impact", "wx-1"),
        )

    def test_invalid_replies_raise(self) -> None:
        cases = {
            "unknown status": reply(status="sunny"),
            "blank assessment id": reply(assessment_id=" "),
        }
        for name, message in cases.items():
            with self.subTest(case=name), self.assertRaises(ValueError):
                message.to_reply()
        wrong_sender = WeatherAssessmentReplyMessage(
            envelope=EnvelopeModel.from_envelope(envelope(AgentName.CONGESTION, reply_to="req-1")),
            status="impact",
        )
        no_reply_to = WeatherAssessmentReplyMessage(
            envelope=EnvelopeModel.from_envelope(envelope(AgentName.WEATHER)), status="impact"
        )
        for message in (wrong_sender, no_reply_to):
            with self.assertRaises(ValueError):
                message.to_reply()

    def test_insufficient_data_needs_no_assessment_id(self) -> None:
        _, status, assessment_id = reply(status="insufficient_data", assessment_id=None).to_reply()
        self.assertEqual((status, assessment_id), ("insufficient_data", None))
