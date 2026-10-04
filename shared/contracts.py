"""Shared domain contracts for observations and the three-agent workflow.

Observation DTOs preserve the implemented SpacetimeDB ingestion interface.
FlowCandidate, WeatherAssessmentRequest and ValidatedAdvisory extend the agent payload catalog;
shared/messages.py provides envelope and weather-exchange wire serialization.
Declared sender roles describe structure, not authentication.
"""

import math
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


class CandidateStatus(StrEnum):
    CHANGE_WORTH_REVIEWING = "change_worth_reviewing"
    INFORMATION_ONLY = "information_only"
    NO_CHANGE = "no_change"
    INSUFFICIENT_DATA = "insufficient_data"


class ActionType(StrEnum):
    """Bounded action catalog. Aircraft-control actions are intentionally absent."""

    REVIEW_FLOW_WINDOW = "review_flow_window"
    REVIEW_RELEASE_RATE = "review_release_rate"
    COORDINATE_DOWNSTREAM_RESTRICTION = "coordinate_downstream_restriction"
    INFORMATION_NOTICE = "information_notice"


class MetricBasis(StrEnum):
    """How a value was obtained; inferred/estimated is never presented as confirmed."""

    OBSERVED = "observed"
    CONFIRMED_RECORD = "confirmed_record"
    INFERRED = "inferred"
    ESTIMATED = "estimated"
    PROJECTED = "projected"


def _check_text(value: object, field_name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field_name} must be a non-empty string")


def _check_number(value: object, field_name: str) -> None:
    if type(value) is int:
        return
    if type(value) is not float or not math.isfinite(value):
        raise ValueError(f"{field_name} must be a finite number")


@dataclass(frozen=True, slots=True)
class TrafficMetric:
    """A named value with explicit unit, basis and the evidence it came from."""

    name: str
    value: int | float
    unit: str
    basis: MetricBasis
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _check_text(self.name, "metric name")
        _check_text(self.unit, "metric unit")
        _check_number(self.value, "metric value")
        if not isinstance(self.basis, MetricBasis):
            raise ValueError("metric basis must be a MetricBasis")
        if not isinstance(self.evidence_ids, tuple) or not self.evidence_ids:
            raise ValueError("A metric needs at least one evidence reference")
        for evidence_id in self.evidence_ids:
            _check_text(evidence_id, "metric evidence_id")


@dataclass(frozen=True, slots=True)
class ActionParameter:
    """One bounded action parameter. Limits are enforced by the validator, not here."""

    name: str
    value: int | float
    unit: str

    def __post_init__(self) -> None:
        _check_text(self.name, "parameter name")
        _check_text(self.unit, "parameter unit")
        _check_number(self.value, "parameter value")


@dataclass(frozen=True, slots=True)
class FlowCandidate:
    """Congestion's combined candidate for validation (plan section 6.3).

    Every metric and FAA event must reference evidence already listed in the
    envelope. Only reliably associated flights appear in ``affected_flight_ids``.
    """

    envelope: MessageEnvelope
    status: CandidateStatus
    issue_type: AdvisoryType
    resource: str
    window_start_utc: datetime
    window_end_utc: datetime
    quality: EvidenceQuality
    affected_flight_ids: tuple[str, ...] = ()
    traffic_metrics: tuple[TrafficMetric, ...] = ()
    weather_assessment_id: str | None = None
    faa_event_ids: tuple[str, ...] = ()
    proposed_action_type: ActionType | None = None
    proposed_action_parameters: tuple[ActionParameter, ...] = ()
    projection_id: str | None = None
    tradeoffs: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    required_missing_inputs: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, MessageEnvelope):
            raise ValueError("envelope must be a MessageEnvelope")
        if self.envelope.sender_agent is not AgentName.CONGESTION:
            raise ValueError("FlowCandidate must be sent by the congestion agent")
        for name, enum_type in (
            ("status", CandidateStatus),
            ("issue_type", AdvisoryType),
            ("quality", EvidenceQuality),
        ):
            if not isinstance(getattr(self, name), enum_type):
                raise ValueError(f"{name} must be a {enum_type.__name__}")
        if self.proposed_action_type is not None and not isinstance(
            self.proposed_action_type, ActionType
        ):
            raise ValueError("proposed_action_type must be an ActionType")
        _check_text(self.resource, "resource")
        for timestamp in (self.window_start_utc, self.window_end_utc):
            if not isinstance(timestamp, datetime) or timestamp.utcoffset() != timedelta(0):
                raise ValueError("Candidate window must use aware UTC timestamps")
        if self.window_end_utc <= self.window_start_utc:
            raise ValueError("Candidate window must end after it starts")

        for name in (
            "affected_flight_ids",
            "faa_event_ids",
            "tradeoffs",
            "assumptions",
            "required_missing_inputs",
            "traffic_metrics",
            "proposed_action_parameters",
        ):
            if not isinstance(getattr(self, name), tuple):
                raise ValueError(f"{name} must be an immutable tuple")
        for name in ("affected_flight_ids", "faa_event_ids"):
            values = getattr(self, name)
            for item in values:
                _check_text(item, name)
            if len(set(values)) != len(values):
                raise ValueError(f"Duplicate entries in {name}")
        for name in ("tradeoffs", "assumptions", "required_missing_inputs"):
            for item in getattr(self, name):
                _check_text(item, name)
        for optional in (self.weather_assessment_id, self.projection_id):
            if optional is not None:
                _check_text(optional, "reference id")
        if not all(isinstance(m, TrafficMetric) for m in self.traffic_metrics):
            raise ValueError("traffic_metrics must contain TrafficMetric values")
        if not all(isinstance(p, ActionParameter) for p in self.proposed_action_parameters):
            raise ValueError("proposed_action_parameters must contain ActionParameter values")

        known = set(self.envelope.evidence_ids)
        for metric in self.traffic_metrics:
            if not set(metric.evidence_ids) <= known:
                raise ValueError("Metric cites evidence missing from the envelope")
        if not set(self.faa_event_ids) <= known:
            raise ValueError("FAA event cites evidence missing from the envelope")
        names = [p.name for p in self.proposed_action_parameters]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate action parameter names")
        self._check_status_rules()

    def _check_status_rules(self) -> None:
        actionable = self.status is CandidateStatus.CHANGE_WORTH_REVIEWING
        if actionable:
            if self.proposed_action_type is None:
                raise ValueError("An actionable candidate needs a proposed action")
            if self.quality is EvidenceQuality.UNAVAILABLE:
                raise ValueError("An actionable candidate cannot have unavailable evidence")
        elif self.proposed_action_parameters:
            raise ValueError("Only an actionable candidate may carry action parameters")
        if self.status in (CandidateStatus.NO_CHANGE, CandidateStatus.INSUFFICIENT_DATA) and (
            self.proposed_action_type is not None
        ):
            raise ValueError("This status must not propose an action")
        if self.status is CandidateStatus.INFORMATION_ONLY and (
            self.proposed_action_type not in (None, ActionType.INFORMATION_NOTICE)
        ):
            raise ValueError("Information-only candidates may only propose a notice")
        if self.status is CandidateStatus.INSUFFICIENT_DATA and not self.required_missing_inputs:
            raise ValueError("insufficient_data must name the missing inputs")
        if self.issue_type is AdvisoryType.DOWNSTREAM_RESTRICTION and actionable:
            if not self.faa_event_ids:
                raise ValueError("A downstream restriction needs an FAA event reference")
            if not self.affected_flight_ids:
                raise ValueError("A downstream restriction needs reliably associated flights")


class AdvisoryPriority(StrEnum):
    INFORMATION = "information"
    ATTENTION = "attention"
    TIME_SENSITIVE = "time_sensitive"


class AdvisoryStatus(StrEnum):
    """Advisory lifecycle (plan section 7). Only controller reducers move past pending."""

    PENDING_REVIEW = "pending_review"
    APPROVED = "approved"
    IGNORED = "ignored"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


MAX_ADVISORY_TEXT = 500


def _check_display_text(value: object, field_name: str) -> None:
    _check_text(value, field_name)
    assert isinstance(value, str)
    if len(value) > MAX_ADVISORY_TEXT or any(ord(c) < 32 for c in value):
        raise ValueError(f"{field_name} must be at most {MAX_ADVISORY_TEXT} printable characters")


def _check_utc(value: object, field_name: str) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError(f"{field_name} must be an aware UTC datetime")


@dataclass(frozen=True, slots=True, kw_only=True)
class ValidatedAdvisory:
    """A validated, versioned advisory awaiting human review (plan section 6.4).

    The validator publishes only ``pending_review``; approval, ignore, expiry and
    supersession are recorded by authorized backend reducers, never by an agent.
    ``material_fingerprint`` lets a later cycle recognise an unchanged advisory.
    """

    advisory_id: str
    version: int
    case_id: str
    snapshot_id: str
    advisory_type: AdvisoryType
    title: str
    suggested_response: str
    reason: str
    priority: AdvisoryPriority
    owner_role: str
    affected_resource: str
    window_start_utc: datetime
    window_end_utc: datetime
    evidence_ids: tuple[str, ...]
    quality: EvidenceQuality
    material_fingerprint: str
    created_at_utc: datetime
    expires_at_utc: datetime
    affected_flight_ids: tuple[str, ...] = ()
    projection_id: str | None = None
    action_type: ActionType | None = None
    action_parameters: tuple[ActionParameter, ...] = ()
    tradeoffs: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    status: AdvisoryStatus = AdvisoryStatus.PENDING_REVIEW
    airport_icao: str = "KDTW"

    def __post_init__(self) -> None:
        if self.status is not AdvisoryStatus.PENDING_REVIEW:
            raise ValueError("Validation publishes pending_review only; controllers decide")
        if self.airport_icao != "KDTW":
            raise ValueError("This project supports airport KDTW")
        for name in ("advisory_id", "case_id", "snapshot_id", "material_fingerprint"):
            _check_text(getattr(self, name), name)
        if type(self.version) is not int or self.version < 1:
            raise ValueError("version must be a positive integer")
        for name, enum_type in (
            ("advisory_type", AdvisoryType),
            ("priority", AdvisoryPriority),
            ("quality", EvidenceQuality),
        ):
            if not isinstance(getattr(self, name), enum_type):
                raise ValueError(f"{name} must be a {enum_type.__name__}")
        if self.quality is EvidenceQuality.UNAVAILABLE:
            raise ValueError("Unavailable evidence yields an internal result, not an advisory")
        for name in ("title", "suggested_response", "reason", "owner_role", "affected_resource"):
            _check_display_text(getattr(self, name), name)
        for name in (
            "window_start_utc",
            "window_end_utc",
            "created_at_utc",
            "expires_at_utc",
        ):
            _check_utc(getattr(self, name), name)
        if self.window_end_utc <= self.window_start_utc:
            raise ValueError("Advisory window must end after it starts")
        if self.expires_at_utc <= self.created_at_utc:
            raise ValueError("Advisory must expire after it is created")

        for name in (
            "evidence_ids",
            "affected_flight_ids",
            "action_parameters",
            "tradeoffs",
            "assumptions",
            "limitations",
        ):
            if not isinstance(getattr(self, name), tuple):
                raise ValueError(f"{name} must be an immutable tuple")
        if not self.evidence_ids:
            raise ValueError("An advisory needs at least one evidence reference")
        for name in ("evidence_ids", "affected_flight_ids"):
            values = getattr(self, name)
            for item in values:
                _check_text(item, name)
            if len(set(values)) != len(values):
                raise ValueError(f"Duplicate entries in {name}")
        for name in ("tradeoffs", "assumptions", "limitations"):
            for item in getattr(self, name):
                _check_display_text(item, name)
        if self.projection_id is not None:
            _check_text(self.projection_id, "projection_id")
            if self.projection_id not in self.evidence_ids:
                raise ValueError("The projection must be listed in evidence_ids")

        if self.action_type is not None and not isinstance(self.action_type, ActionType):
            raise ValueError("action_type must be an ActionType")
        if not all(isinstance(p, ActionParameter) for p in self.action_parameters):
            raise ValueError("action_parameters must contain ActionParameter values")
        if self.action_parameters and self.action_type in (None, ActionType.INFORMATION_NOTICE):
            raise ValueError("Only a flow-review action may carry parameters")
        names = [p.name for p in self.action_parameters]
        if len(set(names)) != len(names):
            raise ValueError("Duplicate action parameter names")


@dataclass(frozen=True, slots=True)
class WeatherAssessmentRequest:
    """Congestion's request for a weather assessment of one fixed snapshot and window.

    ``attempt`` is the exchange number within the case (1 or 2; plan 6.5 allows at most
    two specialist exchanges). Retrying a request reuses the same envelope message_id,
    so receivers can deduplicate.
    """

    MAX_ATTEMPT: ClassVar[int] = 2

    envelope: MessageEnvelope
    window_start_utc: datetime
    window_end_utc: datetime
    attempt: int = 1

    def __post_init__(self) -> None:
        if not isinstance(self.envelope, MessageEnvelope):
            raise ValueError("envelope must be a MessageEnvelope")
        if self.envelope.sender_agent is not AgentName.CONGESTION:
            raise ValueError("WeatherAssessmentRequest must be sent by the congestion agent")
        for timestamp in (self.window_start_utc, self.window_end_utc):
            if not isinstance(timestamp, datetime) or timestamp.utcoffset() != timedelta(0):
                raise ValueError("Assessment window must use aware UTC timestamps")
        if self.window_end_utc <= self.window_start_utc:
            raise ValueError("Assessment window must end after it starts")
        if type(self.attempt) is not int or not 1 <= self.attempt <= self.MAX_ATTEMPT:
            raise ValueError(f"attempt must be an integer from 1 to {self.MAX_ATTEMPT}")


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
    if provider not in {"awc", "adsb_lol", "faa"}:
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
    metar_evidence: "MetarEvidence | None" = None
    taf_evidence: tuple["TafEvidence", ...] = ()

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
        if self.metar_evidence is not None:
            record = self.metar_evidence
            if not isinstance(record, MetarEvidence) or (
                record.observed_at_utc != self.observed_at_utc
                or record.fetched_at_utc != self.fetched_at_utc
                or record.source_ref != self.source_ref
            ):
                raise ValueError("Retained METAR must match the normalized observation")
            c = record.conditions
            visibility = c.visibility_m if c.visibility_bound is VisibilityBound.EXACT else None
            if (
                self.wind_direction_deg,
                self.wind_speed_kt,
                self.wind_gust_kt,
                self.visibility_m,
                self.ceiling_ft,
            ) != (c.wind_direction_deg, c.wind_speed_kt, c.wind_gust_kt, visibility, c.ceiling_ft):
                raise ValueError("Retained METAR measurements disagree")
        if not isinstance(self.taf_evidence, tuple) or len(self.taf_evidence) > 4:
            raise ValueError("Invalid retained TAF collection")
        for forecast in self.taf_evidence:
            if (
                not isinstance(forecast, TafEvidence)
                or forecast.fetched_at_utc > self.fetched_at_utc
            ):
                raise ValueError("TAF fetched after weather commit")


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


class WeatherStatus(StrEnum):
    IMPACT = "impact"
    NO_MATERIAL_IMPACT = "no_material_impact"
    INSUFFICIENT_DATA = "insufficient_data"


class WeatherBasis(StrEnum):
    OBSERVED = "observed"
    FORECAST = "forecast"
    CONDITIONAL = "conditional"
    TRANSITION = "transition"


class VisibilityBound(StrEnum):
    EXACT = "exact"
    GREATER_THAN = "greater_than"
    LESS_THAN = "less_than"


def weather_utc(value: datetime) -> None:
    if not isinstance(value, datetime) or value.utcoffset() != timedelta(0):
        raise ValueError("Weather times must be aware UTC")


def weather_window(start: datetime, end: datetime) -> None:
    weather_utc(start)
    weather_utc(end)
    if end <= start:
        raise ValueError("Weather window must end after start")


@dataclass(frozen=True, slots=True, kw_only=True)
class WeatherConditions:
    wind_direction_deg: float | None = None
    wind_variable: bool = False
    wind_speed_kt: float | None = None
    wind_gust_kt: float | None = None
    visibility_m: float | None = None
    visibility_bound: VisibilityBound = VisibilityBound.EXACT
    ceiling_ft: float | None = None
    sky_known: bool = False
    clear_skies: bool = False
    weather_codes: tuple[str, ...] = ()
    weather_known: bool = False
    temperature_c: float | None = None
    dewpoint_c: float | None = None
    missing_fields: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, limit in (
            ("wind_direction_deg", 360),
            ("wind_speed_kt", 300),
            ("wind_gust_kt", 300),
            ("visibility_m", 200000),
            ("ceiling_ft", 100000),
        ):
            _measurement(getattr(self, name), 0, limit)
        for value in (self.temperature_c, self.dewpoint_c):
            _measurement(value, -100, 100)
        for value in (self.wind_variable, self.sky_known, self.clear_skies, self.weather_known):
            if type(value) is not bool:
                raise ValueError("Weather flags must be booleans")
        if not isinstance(self.visibility_bound, VisibilityBound):
            raise ValueError("Invalid visibility bound")
        if self.wind_variable and self.wind_direction_deg is not None:
            raise ValueError("Variable wind has no fixed direction")
        if self.clear_skies and (not self.sky_known or self.ceiling_ft is not None):
            raise ValueError("Clear skies require known sky and no ceiling")
        for values in (self.weather_codes, self.missing_fields):
            if not isinstance(values, tuple):
                raise ValueError("Weather fields must be immutable")
            for text in values:
                _check_text(text, "weather field")


@dataclass(frozen=True, slots=True, kw_only=True)
class MetarEvidence:
    evidence_id: str
    observed_at_utc: datetime
    fetched_at_utc: datetime
    source_ref: str
    raw_text: str
    conditions: WeatherConditions
    airport_icao: str = "KDTW"

    def __post_init__(self) -> None:
        _weather_evidence(self.evidence_id, self.source_ref, self.airport_icao, self.raw_text)
        weather_utc(self.observed_at_utc)
        weather_utc(self.fetched_at_utc)
        if self.observed_at_utc > self.fetched_at_utc:
            raise ValueError("METAR observation after fetch")
        if not isinstance(self.conditions, WeatherConditions):
            raise ValueError("Invalid weather conditions")


def _weather_evidence(evidence_id: str, source: str, airport: str, raw: str) -> None:
    _check_text(evidence_id, "evidence_id")
    _check_text(source, "source_ref")
    if airport != "KDTW":
        raise ValueError("Weather supports KDTW only")
    if not isinstance(raw, str) or len(raw) > 32768 or len(source) > 2048:
        raise ValueError("Invalid weather source text")


@dataclass(frozen=True, slots=True)
class ForecastGroup:
    start_utc: datetime
    end_utc: datetime
    conditions: WeatherConditions
    change: str = "BASE"
    probability_percent: int | None = None
    transition_end_utc: datetime | None = None

    def __post_init__(self) -> None:
        weather_window(self.start_utc, self.end_utc)
        if not isinstance(self.conditions, WeatherConditions):
            raise ValueError("Invalid forecast conditions")
        if self.change not in {"BASE", "FM", "BECMG", "TEMPO", "PROB"}:
            raise ValueError("Unsupported forecast change")
        if self.probability_percent is not None and (
            type(self.probability_percent) is not int or not 0 < self.probability_percent <= 100
        ):
            raise ValueError("Invalid forecast probability")
        if self.change == "PROB" and self.probability_percent is None:
            raise ValueError("PROB requires probability")
        if self.probability_percent is not None and self.change not in {"PROB", "TEMPO"}:
            raise ValueError("Probability must remain in a conditional group")
        if self.change == "BECMG":
            if self.transition_end_utc is None:
                raise ValueError("BECMG requires transition end")
            weather_utc(self.transition_end_utc)
            if not self.start_utc < self.transition_end_utc <= self.end_utc:
                raise ValueError("Invalid BECMG transition")
        elif self.transition_end_utc is not None:
            raise ValueError("Only BECMG has a transition end")


@dataclass(frozen=True, slots=True)
class ForecastTemperature:
    valid_at_utc: datetime
    temperature_c: float
    kind: str

    def __post_init__(self) -> None:
        weather_utc(self.valid_at_utc)
        _measurement(self.temperature_c, -100, 100)
        if self.temperature_c is None or self.kind not in {"MAX", "MIN"}:
            raise ValueError("Invalid forecast temperature")


@dataclass(frozen=True, slots=True, kw_only=True)
class TafEvidence:
    evidence_id: str
    issued_at_utc: datetime
    fetched_at_utc: datetime
    start_utc: datetime
    end_utc: datetime
    source_ref: str
    raw_text: str
    groups: tuple[ForecastGroup, ...]
    amendment: str = "original"
    airport_icao: str = "KDTW"
    temperatures: tuple[ForecastTemperature, ...] = ()

    def __post_init__(self) -> None:
        _weather_evidence(self.evidence_id, self.source_ref, self.airport_icao, self.raw_text)
        weather_window(self.start_utc, self.end_utc)
        weather_utc(self.issued_at_utc)
        weather_utc(self.fetched_at_utc)
        if self.issued_at_utc > self.fetched_at_utc:
            raise ValueError("TAF issue after fetch")
        if self.amendment not in {"original", "amended", "corrected"}:
            raise ValueError("Invalid TAF amendment")
        if not isinstance(self.groups, tuple):
            raise ValueError("Forecast groups must be immutable")
        for group in self.groups:
            if not isinstance(group, ForecastGroup) or not (
                self.start_utc <= group.start_utc < group.end_utc <= self.end_utc
            ):
                raise ValueError("Forecast group outside TAF validity")
        if not isinstance(self.temperatures, tuple):
            raise ValueError("Forecast temperatures must be immutable")
        for temperature in self.temperatures:
            if not isinstance(temperature, ForecastTemperature) or not (
                self.start_utc <= temperature.valid_at_utc <= self.end_utc
            ):
                raise ValueError("Forecast temperature outside TAF validity")


@dataclass(frozen=True, slots=True, kw_only=True)
class WeatherSnapshot:
    snapshot_id: str
    cutoff_utc: datetime
    evidence_ids: tuple[str, ...]
    metars: tuple[MetarEvidence, ...] = ()
    tafs: tuple[TafEvidence, ...] = ()
    airport_icao: str = "KDTW"
    data_mode: str = "live"

    def __post_init__(self) -> None:
        _check_text(self.snapshot_id, "snapshot_id")
        weather_utc(self.cutoff_utc)
        if self.airport_icao != "KDTW" or self.data_mode != "live":
            raise ValueError("Snapshot must reference live KDTW evidence")
        for values in (self.evidence_ids, self.metars, self.tafs):
            if not isinstance(values, tuple):
                raise ValueError("Snapshot collections must be immutable")
        for value in self.evidence_ids:
            _check_text(value, "evidence_id")
        if len(set(self.evidence_ids)) != len(self.evidence_ids):
            raise ValueError("Duplicate snapshot evidence IDs")
        seen: set[str] = set()
        for record in (*self.metars, *self.tafs):
            if not isinstance(record, (MetarEvidence, TafEvidence)):
                raise ValueError("Invalid weather evidence")
            if record.evidence_id not in self.evidence_ids or record.evidence_id in seen:
                raise ValueError("Missing or duplicate retained weather evidence")
            if record.fetched_at_utc > self.cutoff_utc:
                raise ValueError("Evidence fetched after snapshot cutoff")
            seen.add(record.evidence_id)


@dataclass(frozen=True, slots=True)
class WeatherFact:
    name: str
    value: float
    unit: str
    evidence_id: str
    basis: WeatherBasis
    start_utc: datetime
    end_utc: datetime
    bound: VisibilityBound = VisibilityBound.EXACT
    related_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for value in (self.name, self.unit, self.evidence_id):
            _check_text(value, "weather fact")
        _check_number(self.value, "weather fact value")
        weather_window(self.start_utc, self.end_utc)
        if not isinstance(self.basis, WeatherBasis) or not isinstance(self.bound, VisibilityBound):
            raise ValueError("Invalid fact basis or bound")
        _weather_references(self.related_evidence_ids)


@dataclass(frozen=True, slots=True)
class WeatherHazard:
    kind: str
    area: str
    start_utc: datetime
    end_utc: datetime
    basis: WeatherBasis
    evidence_id: str
    probability_percent: int | None = None
    related_evidence_ids: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.kind not in {
            "wind",
            "gusts",
            "visibility",
            "ceiling",
            "thunderstorm",
            "freezing_precipitation",
            "snow",
            "precipitation",
            "wind_change",
            "gust_change",
            "direction_change",
        }:
            raise ValueError("Unsupported weather hazard")
        if self.area != "KDTW" or not isinstance(self.basis, WeatherBasis):
            raise ValueError("Invalid hazard area or basis")
        _check_text(self.evidence_id, "hazard evidence")
        weather_window(self.start_utc, self.end_utc)
        _weather_references(self.related_evidence_ids)
        if self.probability_percent is not None and (
            type(self.probability_percent) is not int or not 0 < self.probability_percent <= 100
        ):
            raise ValueError("Invalid hazard probability")


def _weather_references(values: tuple[str, ...]) -> None:
    if not isinstance(values, tuple) or len(set(values)) != len(values):
        raise ValueError("Evidence references must be an immutable unique tuple")
    for value in values:
        _check_text(value, "related evidence")


@dataclass(frozen=True, slots=True)
class WeatherImplication:
    implication_id: str
    text: str
    evidence_ids: tuple[str, ...]

    def __post_init__(self) -> None:
        _check_text(self.implication_id, "implication_id")
        _check_text(self.text, "implication text")
        if not isinstance(self.evidence_ids, tuple) or not self.evidence_ids:
            raise ValueError("Implications require immutable evidence references")
        for value in self.evidence_ids:
            _check_text(value, "implication evidence")


@dataclass(frozen=True, slots=True, kw_only=True)
class WeatherAssessment:
    assessment_id: str
    envelope: MessageEnvelope
    window_start_utc: datetime
    window_end_utc: datetime
    status: WeatherStatus
    quality: EvidenceQuality
    hazards: tuple[WeatherHazard, ...]
    weather_facts: tuple[WeatherFact, ...]
    operational_implications: tuple[WeatherImplication, ...]
    missing_inputs: tuple[str, ...]
    assumptions: tuple[str, ...]
    summary: str
    rules_version: str
    prompt_version: str

    def __post_init__(self) -> None:
        for value in (self.assessment_id, self.summary, self.rules_version, self.prompt_version):
            _check_text(value, "assessment field")
        weather_window(self.window_start_utc, self.window_end_utc)
        if not isinstance(self.envelope, MessageEnvelope) or (
            self.envelope.sender_agent is not AgentName.WEATHER
        ):
            raise ValueError("Assessment must belong to weather")
        if not isinstance(self.status, WeatherStatus) or not isinstance(
            self.quality, EvidenceQuality
        ):
            raise ValueError("Invalid weather status or quality")
        for values in (
            self.hazards,
            self.weather_facts,
            self.operational_implications,
            self.missing_inputs,
            self.assumptions,
        ):
            if not isinstance(values, tuple):
                raise ValueError("Assessment collections must be immutable")
        known = set(self.envelope.evidence_ids)
        for item in (*self.hazards, *self.weather_facts):
            if not isinstance(item, (WeatherHazard, WeatherFact)):
                raise ValueError("Invalid weather assessment item")
            if item.evidence_id not in known or not set(item.related_evidence_ids) <= known:
                raise ValueError("Assessment cites unknown evidence")
            if not self.window_start_utc <= item.start_utc < item.end_utc <= self.window_end_utc:
                raise ValueError("Assessment item outside requested window")
        for implication in self.operational_implications:
            if not isinstance(implication, WeatherImplication):
                raise ValueError("Invalid weather implication")
            if not set(implication.evidence_ids) <= known:
                raise ValueError("Implication cites unknown evidence")
        if self.status is WeatherStatus.IMPACT and not self.hazards:
            raise ValueError("Impact requires supported hazards")
        if self.status is WeatherStatus.NO_MATERIAL_IMPACT and (
            self.hazards or self.missing_inputs
        ):
            raise ValueError("Clear assessment requires complete evidence")
        if self.status is WeatherStatus.INSUFFICIENT_DATA and not self.missing_inputs:
            raise ValueError("Insufficient data must name missing inputs")
