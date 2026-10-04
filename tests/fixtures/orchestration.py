"""Synthetic reference packets for coordination tests, never provider observations."""

from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from agents.validation.orchestrator import Dispatch
from shared.contracts import AgentName, MessageEnvelope
from shared.orchestration import CandidateReady

TEST_ADDRESSES = {
    AgentName.WEATHER: "test-weather-address",
    AgentName.CONGESTION: "test-congestion-address",
    AgentName.VALIDATION: "test-validation-address",
}


def opening(now: datetime | None = None) -> MessageEnvelope:
    moment = now or datetime.now(UTC)
    return MessageEnvelope(
        schema_version=1,
        message_id="test-open-message",
        case_id="test-case",
        sender_agent=AgentName.WEATHER,
        created_at_utc=moment,
        snapshot_id="test-snapshot",
        evidence_ids=("test-weather-record", "test-traffic-record"),
        airport_icao="KDTW",
        valid_until_utc=moment + timedelta(minutes=2),
    )


def candidate(dispatch: Dispatch, now: datetime | None = None) -> CandidateReady:
    envelope = replace(
        dispatch.request.envelope,
        sender_agent=AgentName.CONGESTION,
        message_id=str(uuid4()),
        created_at_utc=now or dispatch.request.envelope.created_at_utc,
        reply_to=dispatch.request.envelope.message_id,
    )
    return CandidateReady(
        envelope=envelope,
        candidate_id=f"test-candidate-{dispatch.request.assessment_round}",
        weather_assessment_id="test-weather-assessment",
        assessment_round=dispatch.request.assessment_round,
    )
