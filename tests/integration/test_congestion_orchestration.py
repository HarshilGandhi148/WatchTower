"""Congestion's replies satisfy the validation orchestrator, across both rounds and the wire.

Cross-owner integration test: it drives the real Orchestrator (validation) with the real
CongestionService (congestion) and serializes every message through shared/messages.py.
No agents, servers or providers are started.
"""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import TestCase

from agents.congestion.feeds import FeedSnapshot
from agents.congestion.service import CongestionService
from agents.validation.orchestrator import OrchestrationError, Orchestrator
from data.adsb import parse_point_response
from shared.contracts import AgentName, MessageEnvelope
from shared.messages import (
    AssessmentRequestMessage,
    CandidateReadyMessage,
    CaseOpenedMessage,
    EnvelopeModel,
    WeatherAssessmentReplyMessage,
    WeatherAssessmentRequestMessage,
)
from shared.orchestration import CasePhase, ValidationVerdict
from tests.fixtures.orchestration import TEST_ADDRESSES

ROOT = Path(__file__).resolve().parents[2]
ADSB = json.loads((ROOT / "tests" / "fixtures" / "adsb" / "point_response.json").read_bytes())
T0 = datetime.now(UTC).replace(microsecond=0)  # the orchestrator checks against wall time
CONGESTION = TEST_ADDRESSES[AgentName.CONGESTION]


def wire(model: object) -> object:
    """Serialize and parse back, as the SDK transport would."""
    cls = type(model)
    return cls.parse_raw(model.json())  # type: ignore[attr-defined]


class CongestionOrchestrationTests(TestCase):
    def setUp(self) -> None:
        self.service = CongestionService()
        self.coordinator = Orchestrator(TEST_ADDRESSES)
        feeds = FeedSnapshot(parse_point_response(ADSB, T0), None)
        opening = self.service.start_cycle(feeds, T0)
        message = wire(CaseOpenedMessage(envelope=EnvelopeModel.from_envelope(opening)))
        dispatch = self.coordinator.open_case(CONGESTION, message.envelope.to_envelope(), now=T0)  # type: ignore[attr-defined]
        assert dispatch is not None
        self.dispatch = dispatch

    def round_trip(self, dispatch_request: object, at: datetime, status: str, wx_id: str) -> object:
        request = wire(AssessmentRequestMessage.from_request(dispatch_request))  # type: ignore[arg-type]
        weather_request = self.service.on_assessment_request(request.to_request(), at)  # type: ignore[attr-defined]
        sent = wire(WeatherAssessmentRequestMessage.from_request(weather_request))
        received = sent.to_request()  # type: ignore[attr-defined]  # what the weather agent sees
        reply_env = MessageEnvelope(
            schema_version=1,
            message_id=f"weather-reply-{received.attempt}",
            case_id=received.envelope.case_id,
            sender_agent=AgentName.WEATHER,
            created_at_utc=at,
            snapshot_id=received.envelope.snapshot_id,
            evidence_ids=(),
            airport_icao="KDTW",
            valid_until_utc=at + timedelta(minutes=1),
            reply_to=received.envelope.message_id,
        )
        reply = wire(
            WeatherAssessmentReplyMessage(
                envelope=EnvelopeModel.from_envelope(reply_env), status=status, assessment_id=wx_id
            )
        )
        envelope, got_status, got_id = reply.to_reply()  # type: ignore[attr-defined]
        self.service.on_weather_reply(envelope, got_status, got_id, at)
        (ready,) = self.service.advance(at + timedelta(seconds=1)).ready
        message = wire(
            CandidateReadyMessage(
                envelope=EnvelopeModel.from_envelope(ready.envelope),
                candidate_id=ready.candidate_id,
                weather_assessment_id=ready.weather_assessment_id,
                assessment_round=ready.assessment_round,
                projection_id=ready.projection_id,
            )
        )
        return message.to_candidate()  # type: ignore[attr-defined]

    def test_round_one_candidate_is_accepted_by_the_orchestrator(self) -> None:
        candidate = self.round_trip(self.dispatch.request, T0, "no_material_impact", "wx-1")
        accepted = self.coordinator.receive_candidate(
            CONGESTION, candidate, now=T0 + timedelta(seconds=2)
        )  # type: ignore[arg-type]
        self.assertEqual(accepted, candidate)
        self.assertEqual(
            self.coordinator.get_case(candidate.envelope.case_id).phase, CasePhase.VALIDATING
        )  # type: ignore[attr-defined]

    def test_reassessment_round_is_accepted_and_then_published(self) -> None:
        first = self.round_trip(self.dispatch.request, T0, "no_material_impact", "wx-1")
        self.coordinator.receive_candidate(CONGESTION, first, now=T0 + timedelta(seconds=2))  # type: ignore[arg-type]
        redo = self.coordinator.finish_validation(
            first, ValidationVerdict.REASSESS, now=T0 + timedelta(seconds=3)
        )  # type: ignore[arg-type]
        assert redo is not None
        second = self.round_trip(redo.request, T0 + timedelta(seconds=4), "impact", "wx-2")
        self.assertEqual(second.assessment_round, 2)  # type: ignore[attr-defined]
        self.coordinator.receive_candidate(CONGESTION, second, now=T0 + timedelta(seconds=6))  # type: ignore[arg-type]
        self.coordinator.finish_validation(
            second, ValidationVerdict.PUBLISH, now=T0 + timedelta(seconds=7)
        )  # type: ignore[arg-type]
        case = self.coordinator.get_case(second.envelope.case_id)  # type: ignore[attr-defined]
        self.assertEqual(case.phase, CasePhase.READY_TO_PUBLISH)

    def test_a_stale_round_one_candidate_is_rejected_after_reassessment(self) -> None:
        first = self.round_trip(self.dispatch.request, T0, "no_material_impact", "wx-1")
        self.coordinator.receive_candidate(CONGESTION, first, now=T0 + timedelta(seconds=2))  # type: ignore[arg-type]
        self.coordinator.finish_validation(
            first, ValidationVerdict.REASSESS, now=T0 + timedelta(seconds=3)
        )  # type: ignore[arg-type]
        from dataclasses import replace

        stale = replace(first, envelope=replace(first.envelope, message_id="replayed"))  # type: ignore[attr-defined]
        with self.assertRaises(OrchestrationError):
            self.coordinator.receive_candidate(CONGESTION, stale, now=T0 + timedelta(seconds=4))
