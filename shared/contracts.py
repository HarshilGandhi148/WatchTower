"""Initial cross-agent envelope and bounded catalog from technical plan section 6.

Payload DTOs and uAgents serialization require a separate reviewed implementation.
The declared sender role is structural information, not authentication.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import ClassVar


class AgentName(StrEnum):
    WEATHER = "weather"
    CONGESTION = "congestion"
    VALIDATION = "validation"


class AdvisoryType(StrEnum):
    WEATHER_FLOW_REVIEW = "WEATHER_FLOW_REVIEW"
    DEPARTURE_RELEASE_REVIEW = "DEPARTURE_RELEASE_REVIEW"
    DOWNSTREAM_RESTRICTION = "DOWNSTREAM_RESTRICTION"
    TRAFFIC_PRESSURE_NOTICE = "TRAFFIC_PRESSURE_NOTICE"


class EvidenceQuality(StrEnum):
    ADEQUATE = "adequate"
    LIMITED = "limited"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class MessageEnvelope:
    """Immutable, UTC-aware references to a case and its stored evidence."""

    SUPPORTED_SCHEMA_VERSION: ClassVar[int] = 1

    schema_version: int
    message_id: str
    case_id: str
    sender_agent: AgentName
    created_at_utc: datetime
    snapshot_id: str
    evidence_ids: tuple[str, ...]
    airport_icao: str
    valid_until_utc: datetime
    correlation_id: str | None = None
    reply_to: str | None = None

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or (
            self.schema_version != self.SUPPORTED_SCHEMA_VERSION
        ):
            raise ValueError("Unsupported message schema version")
        if not isinstance(self.sender_agent, AgentName):
            raise ValueError("sender_agent must be a registered AgentName")
        if self.airport_icao != "KDTW":
            raise ValueError("This project supports airport KDTW")
        for field_name in ("message_id", "case_id", "snapshot_id"):
            self._check_id(getattr(self, field_name), field_name)
        if not isinstance(self.evidence_ids, tuple):
            raise ValueError("evidence_ids must be an immutable tuple")
        for evidence_id in self.evidence_ids:
            self._check_id(evidence_id, "evidence_id")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Duplicate evidence references")
        for field_name in ("correlation_id", "reply_to"):
            value = getattr(self, field_name)
            if value is not None:
                self._check_id(value, field_name)
        for timestamp in (self.created_at_utc, self.valid_until_utc):
            if not isinstance(timestamp, datetime) or timestamp.utcoffset() != timedelta(0):
                raise ValueError("Message timestamps must be aware and use UTC")
        if self.valid_until_utc <= self.created_at_utc:
            raise ValueError("Message validity must end after creation")

    @staticmethod
    def _check_id(value: object, field_name: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field_name} must be a non-empty string")
