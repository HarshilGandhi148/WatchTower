"""Fetch.ai wire models, with explicit conversion to immutable domain contracts.

uAgents 0.26 uses Pydantic v1 models; it cannot directly validate slotted domain
dataclasses. Keep this boundary explicit and preserve all envelope checks.
"""

from datetime import datetime
from typing import Any

from pydantic.v1 import StrictInt, StrictStr
from uagents import Model

from shared.contracts import AgentName, MessageEnvelope
from shared.orchestration import AssessmentRequest, CandidateReady


class WireModel(Model):
    class Config:
        extra = "forbid"
        allow_mutation = False


class EnvelopeModel(WireModel):
    schema_version: StrictInt
    message_id: StrictStr
    case_id: StrictStr
    sender_agent: AgentName
    created_at_utc: datetime
    snapshot_id: StrictStr
    evidence_ids: tuple[StrictStr, ...]
    airport_icao: StrictStr
    valid_until_utc: datetime
    correlation_id: StrictStr | None = None
    reply_to: StrictStr | None = None

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)
        self.to_envelope()

    @classmethod
    def from_envelope(cls, envelope: MessageEnvelope) -> "EnvelopeModel":
        return cls(
            schema_version=envelope.schema_version,
            message_id=envelope.message_id,
            case_id=envelope.case_id,
            sender_agent=envelope.sender_agent,
            created_at_utc=envelope.created_at_utc,
            snapshot_id=envelope.snapshot_id,
            evidence_ids=envelope.evidence_ids,
            airport_icao=envelope.airport_icao,
            valid_until_utc=envelope.valid_until_utc,
            correlation_id=envelope.correlation_id,
            reply_to=envelope.reply_to,
        )

    def to_envelope(self) -> MessageEnvelope:
        return MessageEnvelope(
            schema_version=self.schema_version,
            message_id=self.message_id,
            case_id=self.case_id,
            sender_agent=self.sender_agent,
            created_at_utc=self.created_at_utc,
            snapshot_id=self.snapshot_id,
            evidence_ids=self.evidence_ids,
            airport_icao=self.airport_icao,
            valid_until_utc=self.valid_until_utc,
            correlation_id=self.correlation_id,
            reply_to=self.reply_to,
        )


class CaseOpenedMessage(WireModel):
    envelope: EnvelopeModel


class AssessmentRequestMessage(WireModel):
    envelope: EnvelopeModel
    assessment_round: StrictInt

    @classmethod
    def from_request(cls, request: AssessmentRequest) -> "AssessmentRequestMessage":
        return cls(
            envelope=EnvelopeModel.from_envelope(request.envelope),
            assessment_round=request.assessment_round,
        )

    def to_request(self) -> AssessmentRequest:
        return AssessmentRequest(self.envelope.to_envelope(), self.assessment_round)


class CandidateReadyMessage(WireModel):
    envelope: EnvelopeModel
    candidate_id: StrictStr
    weather_assessment_id: StrictStr
    assessment_round: StrictInt
    projection_id: StrictStr | None = None

    def to_candidate(self) -> CandidateReady:
        return CandidateReady(
            self.envelope.to_envelope(),
            self.candidate_id,
            self.weather_assessment_id,
            self.assessment_round,
            self.projection_id,
        )
