"""Stored-record references and lifecycle types for the validation coordinator."""

from dataclasses import dataclass
from enum import StrEnum

from shared.contracts import MessageEnvelope


class CasePhase(StrEnum):
    ASSESSING = "assessing"
    VALIDATING = "validating"
    READY_TO_PUBLISH = "ready_to_publish"
    REJECTED = "rejected"
    INSUFFICIENT_DATA = "insufficient_data"


class ValidationVerdict(StrEnum):
    PUBLISH = "publish"
    REASSESS = "reassess"
    REJECT = "reject"
    INSUFFICIENT_DATA = "insufficient_data"


@dataclass(frozen=True, slots=True)
class AssessmentRequest:
    """Ask congestion to coordinate weather and traffic for one fixed snapshot."""

    envelope: MessageEnvelope
    assessment_round: int

    def __post_init__(self) -> None:
        check_round(self.assessment_round)


@dataclass(frozen=True, slots=True)
class CandidateReady:
    """References only; a domain validator must load and check the stored packet."""

    envelope: MessageEnvelope
    candidate_id: str
    weather_assessment_id: str
    assessment_round: int
    projection_id: str | None = None

    def __post_init__(self) -> None:
        check_round(self.assessment_round)
        for reference in (self.candidate_id, self.weather_assessment_id):
            if not isinstance(reference, str) or not reference.strip():
                raise ValueError("Candidate and weather assessment references are required")
        if self.projection_id is not None and not self.projection_id.strip():
            raise ValueError("An optional projection reference must not be blank")


def check_round(value: int) -> None:
    if type(value) is not int or value not in (1, 2):
        raise ValueError("Only an initial assessment and one reassessment are allowed")
