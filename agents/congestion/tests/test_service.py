"""Congestion side of the case protocol, plus the interim live-feed fetch."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import TestCase

from agents.congestion.feeds import FeedSnapshot
from agents.congestion.service import CongestionService, ProtocolError
from data.adsb import parse_point_response
from shared.contracts import AgentName, CandidateStatus, MessageEnvelope
from shared.orchestration import AssessmentRequest

ROOT = Path(__file__).resolve().parents[3]
ADSB_BODY = (ROOT / "tests" / "fixtures" / "adsb" / "point_response.json").read_bytes()
NOW = datetime(2026, 9, 21, 4, 53, 21, tzinfo=UTC)


def at(seconds: float) -> datetime:
    return NOW + timedelta(seconds=seconds)


def validation_request(
    opening: MessageEnvelope, round_: int = 1, reply_to: str | None = None, **changes: object
) -> AssessmentRequest:
    """What validation's orchestrator sends (shape mirrors agents/validation)."""
    envelope = replace(
        opening,
        message_id=f"validation-request-{round_}",
        sender_agent=AgentName.VALIDATION,
        created_at_utc=at(1),
        valid_until_utc=at(61),
        correlation_id=opening.message_id,
        reply_to=reply_to or opening.message_id,
    )
    return AssessmentRequest(replace(envelope, **changes), round_)  # type: ignore[arg-type]


def weather_reply(
    request_id: str, opening: MessageEnvelope, when: datetime, n: int = 1
) -> MessageEnvelope:
    return MessageEnvelope(
        schema_version=1,
        message_id=f"weather-reply-{n}",
        case_id=opening.case_id,
        sender_agent=AgentName.WEATHER,
        created_at_utc=when,
        snapshot_id=opening.snapshot_id,
        evidence_ids=(),
        airport_icao="KDTW",
        valid_until_utc=when + timedelta(minutes=1),
        reply_to=request_id,
    )


class ServiceProtocolTests(TestCase):
    def setUp(self) -> None:
        self.feeds = FeedSnapshot(
            parse_point_response(json.loads(ADSB_BODY), NOW),
            None,
            snapshot_id="TEST-snapshot",
            case_id="TEST-case",
            evidence_ids=("TEST-adsb",),
            traffic_evidence_id="TEST-adsb",
            window_start_utc=NOW,
            window_end_utc=NOW + timedelta(minutes=15),
            expires_at_utc=at(120),
        )
        self.service = CongestionService()
        self.opening = self.service.start_cycle(self.feeds, NOW)

    def answer_round_one(self) -> str:
        weather = self.service.on_assessment_request(validation_request(self.opening), at(2))
        result = self.service.on_weather_reply(
            weather_reply(weather.envelope.message_id, self.opening, at(3)),
            "no_material_impact",
            "wx-1",
            at(3),
        )
        self.assertEqual(result.outcome.value, "accepted")
        return weather.envelope.message_id

    def test_opening_fixes_snapshot_and_evidence(self) -> None:
        o = self.opening
        self.assertEqual((o.sender_agent, o.case_id), (AgentName.CONGESTION, "TEST-case"))
        self.assertEqual(o.evidence_ids, ("TEST-adsb",))
        self.assertEqual(self.service.start_cycle(self.feeds, NOW), o)  # same snapshot: idempotent

    def test_round_one_produces_one_weather_request_and_retries_reuse_it(self) -> None:
        request = validation_request(self.opening)
        weather = self.service.on_assessment_request(request, at(2))
        self.assertEqual(weather.attempt, 1)
        self.assertEqual(weather.envelope.snapshot_id, self.opening.snapshot_id)
        self.assertEqual(self.service.on_assessment_request(request, at(3)), weather)

    def test_candidate_ready_follows_the_validation_reply_rules(self) -> None:
        self.answer_round_one()
        (ready,) = self.service.advance(at(4)).ready
        e = ready.envelope
        self.assertEqual(e.sender_agent, AgentName.CONGESTION)
        self.assertEqual(e.reply_to, "validation-request-1")
        self.assertEqual(e.correlation_id, self.opening.message_id)
        self.assertEqual(set(e.evidence_ids), set(self.opening.evidence_ids))
        self.assertEqual(e.valid_until_utc, at(61))
        self.assertEqual((ready.weather_assessment_id, ready.assessment_round), ("wx-1", 1))
        self.assertIn(ready.candidate_id, self.service.candidates)
        self.service.mark_persisted(ready)
        self.assertEqual(self.service.advance(at(5)).ready, ())  # each round sent once

    def test_round_two_reassesses_once_and_replies_to_the_new_request(self) -> None:
        self.answer_round_one()
        (first,) = self.service.advance(at(4)).ready
        self.service.mark_persisted(first)
        second_request = validation_request(self.opening, 2, reply_to=first.envelope.message_id)
        weather = self.service.on_assessment_request(second_request, at(5))
        self.assertEqual(weather.attempt, 2)
        self.service.on_weather_reply(
            weather_reply(weather.envelope.message_id, self.opening, at(6), 2),
            "impact",
            "wx-2",
            at(6),
        )
        (second,) = self.service.advance(at(7)).ready
        self.assertEqual(second.assessment_round, 2)
        self.assertEqual(second.envelope.reply_to, "validation-request-2")
        self.assertEqual(second.weather_assessment_id, "wx-2")
        self.assertNotEqual(second.candidate_id, first.candidate_id)

    def test_weather_timeout_is_not_submitted_without_an_assessment(self) -> None:
        self.service.on_assessment_request(validation_request(self.opening), at(2))
        step = self.service.advance(at(20))
        self.assertEqual((step.ready, step.abandoned), ((), (self.opening.case_id,)))
        self.assertEqual(self.service.advance(at(21)).abandoned, ())  # reported once

    def test_insufficient_weather_reaches_validation_with_its_finding(self) -> None:
        weather = self.service.on_assessment_request(validation_request(self.opening), at(2))
        self.service.on_weather_reply(
            weather_reply(weather.envelope.message_id, self.opening, at(3)),
            "insufficient_data",
            "wx-1",
            at(3),
        )
        step = self.service.advance(at(4))
        self.assertEqual(step.abandoned, ())
        (ready,) = step.ready
        self.assertEqual(ready.weather_assessment_id, "wx-1")
        self.assertEqual(self.service.candidates[ready.candidate_id].weather_assessment_id, "wx-1")

    def test_mismatched_or_out_of_order_requests_are_protocol_errors(self) -> None:
        cases = {
            "unknown case": validation_request(self.opening, case_id="case-other"),
            "other snapshot": validation_request(self.opening, snapshot_id="snap-other"),
            "other evidence": validation_request(self.opening, evidence_ids=("adsb-x",)),
            "not from validation": validation_request(self.opening, sender_agent=AgentName.WEATHER),
            "round 2 first": validation_request(self.opening, 2),
        }
        for name, request in cases.items():
            with self.subTest(case=name), self.assertRaises(ProtocolError):
                self.service.on_assessment_request(request, at(2))
        with self.assertRaises(ProtocolError):
            self.service.on_assessment_request(validation_request(self.opening), at(90))  # expired

    def test_cases_validation_never_asked_about_are_forgotten(self) -> None:
        self.service.advance(at(130))
        self.assertEqual(self.service.open_cases, ())

    def test_failed_feeds_still_open_a_case_with_no_evidence(self) -> None:
        service = CongestionService()
        opening = service.start_cycle(
            replace(self.feeds, adsb=None, evidence_ids=(), traffic_evidence_id=None), NOW
        )
        self.assertEqual(opening.evidence_ids, ())
        weather = service.on_assessment_request(validation_request(opening), at(2))
        service.on_weather_reply(
            weather_reply(weather.envelope.message_id, opening, at(3)),
            "no_material_impact",
            "wx",
            at(3),
        )
        (ready,) = service.advance(at(4)).ready
        self.assertEqual(
            service.candidates[ready.candidate_id].status, CandidateStatus.INSUFFICIENT_DATA
        )
