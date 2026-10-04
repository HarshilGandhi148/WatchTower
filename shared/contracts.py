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


# Observation DTOs are infrastructure contracts, not additional application agents.
def _utc_millis(value: datetime) -> str:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError("Observation timestamps must use aware UTC")
    if value.year < 1970 or value.microsecond % 1000:
        raise ValueError("Observation timestamps require nonnegative millisecond precision")
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _measurement(value: float | None, minimum: float, maximum: float) -> None:
    import math

    if value is not None and (
        isinstance(value, bool)
        or not isinstance(value, (float, int))
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ValueError("Invalid observation measurement")


def _source(provider: str, source_ref: str, fetched: datetime) -> None:
    if provider not in {"awc", "adsb_lol"}:
        raise ValueError("Unsupported observation provider")
    if not source_ref.strip() or len(source_ref) > 2048 or any(ord(c) < 32 for c in source_ref):
        raise ValueError("Invalid source reference")
    _utc_millis(fetched)


@dataclass(frozen=True, slots=True, kw_only=True)
class WeatherRecord:
    """Normalized KDTW METAR; absent measurements remain unknown."""

    observed_at_utc: datetime
    fetched_at_utc: datetime
    source_ref: str
    wind_direction_deg: float | None = None
    wind_speed_kt: float | None = None
    wind_gust_kt: float | None = None
    visibility_m: float | None = None
    ceiling_ft: float | None = None
    provider: str = "awc"
    airport_icao: str = "KDTW"
    record_type: str = "METAR"

    def __post_init__(self) -> None:
        _source(self.provider, self.source_ref, self.fetched_at_utc)
        _utc_millis(self.observed_at_utc)
        if self.provider != "awc" or self.airport_icao != "KDTW" or self.record_type != "METAR":
            raise ValueError("Unsupported weather record")
        if self.observed_at_utc > self.fetched_at_utc:
            raise ValueError("Observation after fetch")
        for value, limit in (
            (self.wind_direction_deg, 360),
            (self.wind_speed_kt, 300),
            (self.wind_gust_kt, 300),
            (self.visibility_m, 200_000),
            (self.ceiling_ft, 100_000),
        ):
            _measurement(value, 0, limit)


@dataclass(frozen=True, slots=True, kw_only=True)
class AircraftObservation:
    """A nearby aircraft observation; airport association is not implied."""

    aircraft_id: str
    observed_at_utc: datetime
    latitude_deg: float | None = None
    longitude_deg: float | None = None
    altitude_ft: float | None = None
    speed_kt: float | None = None
    track_deg: float | None = None
    position_age_s: float | None = None
    on_ground: bool | None = None

    def __post_init__(self) -> None:
        if (
            not self.aircraft_id.strip()
            or len(self.aircraft_id) > 128
            or any(ord(c) < 32 for c in self.aircraft_id)
        ):
            raise ValueError("Invalid aircraft ID")
        _utc_millis(self.observed_at_utc)
        if (self.latitude_deg is None) != (self.longitude_deg is None):
            raise ValueError("Coordinates must be a pair")
        if self.on_ground is not None and type(self.on_ground) is not bool:
            raise ValueError("Invalid on_ground")
        for value, minimum, maximum in (
            (self.latitude_deg, -90, 90),
            (self.longitude_deg, -180, 180),
            (self.altitude_ft, -2000, 100_000),
            (self.speed_kt, 0, 2000),
            (self.track_deg, 0, 360),
            (self.position_age_s, 0, 86400),
        ):
            _measurement(value, minimum, maximum)


@dataclass(frozen=True, slots=True, kw_only=True)
class AircraftBatch:
    fetched_at_utc: datetime
    source_ref: str
    observations: tuple[AircraftObservation, ...]
    provider: str = "adsb_lol"

    def __post_init__(self) -> None:
        _source(self.provider, self.source_ref, self.fetched_at_utc)
        if self.provider != "adsb_lol" or not isinstance(self.observations, tuple):
            raise ValueError("Invalid aircraft batch")
        if len(self.observations) > 1000:
            raise ValueError("Aircraft batch exceeds 1000 observations")
        seen: set[str] = set()
        for observation in self.observations:
            if not isinstance(observation, AircraftObservation):
                raise ValueError("Expected AircraftObservation")
            if observation.aircraft_id in seen:
                raise ValueError("Duplicate aircraft")
            seen.add(observation.aircraft_id)
            if observation.observed_at_utc > self.fetched_at_utc:
                raise ValueError("Observation after fetch")


@dataclass(frozen=True, slots=True, kw_only=True)
class SourceFailure:
    provider: str
    source_ref: str
    fetched_at_utc: datetime

    def __post_init__(self) -> None:
        _source(self.provider, self.source_ref, self.fetched_at_utc)
