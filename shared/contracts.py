"""Initial cross-agent envelope and bounded catalog from technical plan section 6.

FlowCandidate (plan section 6.3) is the first payload DTO. uAgents serialization
still requires a separate reviewed implementation. The declared sender role is
structural information, not authentication.
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
