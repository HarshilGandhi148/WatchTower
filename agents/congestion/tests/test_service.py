"""Congestion side of the case protocol, plus the interim live-feed fetch."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import TestCase

from agents.congestion.feeds import FeedConfig, FeedSnapshot, fetch_feeds
from agents.congestion.service import CongestionService, ProtocolError
from data.adsb import parse_point_response
from shared.contracts import AgentName, CandidateStatus, MessageEnvelope
from shared.orchestration import AssessmentRequest

ROOT = Path(__file__).resolve().parents[3]
ADSB_BODY = (ROOT / "tests" / "fixtures" / "adsb" / "point_response.json").read_bytes()
FAA_BODY = (ROOT / "tests" / "fixtures" / "faa_status" / "events.xml").read_bytes()
NOW = datetime(2026, 9, 21, 4, 53, 21, tzinfo=UTC)
FEED_CONFIG = FeedConfig(
    "https://api.adsb.lol",
    "https://nasstatus.faa.gov/api/airport-status-information",
    "WatchTower-tests (TEST)",
    (42.2122, -83.3533),
)


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
        self.feeds = FeedSnapshot(parse_point_response(json.loads(ADSB_BODY), NOW), None)
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
        self.assertEqual(
            (o.sender_agent, o.case_id), (AgentName.CONGESTION, "case-20260921T045321Z")
        )
        self.assertEqual(o.evidence_ids, ("adsb-20260921T045321Z",))
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
        self.assertEqual(self.service.advance(at(5)).ready, ())  # each round sent once

    def test_round_two_reassesses_once_and_replies_to_the_new_request(self) -> None:
        self.answer_round_one()
        (first,) = self.service.advance(at(4)).ready
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

    def test_insufficient_weather_has_no_assessment_to_reference(self) -> None:
        weather = self.service.on_assessment_request(validation_request(self.opening), at(2))
        self.service.on_weather_reply(
            weather_reply(weather.envelope.message_id, self.opening, at(3)),
            "insufficient_data",
            "wx-1",
            at(3),
        )
        self.assertEqual(self.service.advance(at(4)).abandoned, (self.opening.case_id,))

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
        opening = service.start_cycle(FeedSnapshot(None, None, ("adsb: down",)), NOW)
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


def fake_get(responses: dict[str, bytes | Exception]):  # type: ignore[no-untyped-def]
    calls: list[tuple[str, str]] = []

    def get(url: str, user_agent: str) -> bytes:
        calls.append((url, user_agent))
        for prefix, response in responses.items():
            if url.startswith(prefix):
                if isinstance(response, Exception):
                    raise response
                return response
        raise AssertionError(f"unexpected url {url}")

    get.calls = calls  # type: ignore[attr-defined]
    return get


class FeedTests(TestCase):
    def test_both_feeds_parse_and_send_the_user_agent(self) -> None:
        get = fake_get({"https://api.adsb.lol": ADSB_BODY, "https://nasstatus": FAA_BODY})
        feeds = fetch_feeds(FEED_CONFIG, get, clock=lambda: NOW)
        assert feeds.adsb and feeds.faa
        self.assertEqual(feeds.errors, ())
        self.assertIn("/v2/point/42.2122/-83.3533/40", get.calls[0][0])
        self.assertTrue(all(ua == "WatchTower-tests (TEST)" for _, ua in get.calls))

    def test_one_failing_feed_is_unknown_and_the_other_still_works(self) -> None:
        get = fake_get(
            {"https://api.adsb.lol": OSError("timed out"), "https://nasstatus": FAA_BODY}
        )
        feeds = fetch_feeds(FEED_CONFIG, get, clock=lambda: NOW)
        self.assertIsNone(feeds.adsb)
        self.assertIsNotNone(feeds.faa)
        self.assertEqual(len(feeds.errors), 1)

    def test_malformed_bodies_are_errors_not_empty_feeds(self) -> None:
        get = fake_get({"https://api.adsb.lol": b"<html>503</html>", "https://nasstatus": b"{}"})
        feeds = fetch_feeds(FEED_CONFIG, get, clock=lambda: NOW)
        self.assertEqual((feeds.adsb, feeds.faa, len(feeds.errors)), (None, None, 2))
