"""Versioned storage DTOs/serialization shared by specialists and infrastructure.

No provider or agent implementation imports. Candidate delivery acknowledges receipt
only; these records never publish an advisory or record a controller decision.
"""

import json
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta
from typing import Any

from shared.contracts import (
    ActionParameter,
    ActionType,
    AdvisoryType,
    AgentName,
    CandidateStatus,
    EvidenceQuality,
    FlowCandidate,
    MessageEnvelope,
    MetricBasis,
    TrafficMetric,
    _utc_millis,
)
from shared.messages import CandidateReadyMessage, EnvelopeModel
from shared.orchestration import CandidateReady


def storage_json(value: object) -> str:
    def encode(item: object) -> object:
        if isinstance(item, datetime):
            # Provider-derived fractional position times use millisecond storage.
            return _utc_millis(item.replace(microsecond=item.microsecond // 1000 * 1000))
        if is_dataclass(item) and not isinstance(item, type):
            return asdict(item)
        raise TypeError("Unsupported storage value")

    return json.dumps(value, default=encode, allow_nan=False, separators=(",", ":"))


def utc(value: str) -> datetime:
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.utcoffset() != timedelta(0):
        raise ValueError("Stored timestamps must use UTC")
    return result


def envelope_from_dict(value: dict[str, Any]) -> MessageEnvelope:
    return EnvelopeModel(**value).to_envelope()


def candidate_from_dict(value: dict[str, Any]) -> FlowCandidate:
    v = dict(value)
    v["envelope"] = envelope_from_dict(v["envelope"])
    for name in ("window_start_utc", "window_end_utc"):
        v[name] = utc(v[name])
    for name, enum in (
        ("status", CandidateStatus),
        ("issue_type", AdvisoryType),
        ("quality", EvidenceQuality),
    ):
        v[name] = enum(v[name])
    if v["proposed_action_type"] is not None:
        v["proposed_action_type"] = ActionType(v["proposed_action_type"])
    v["traffic_metrics"] = tuple(
        TrafficMetric(
            m["name"], m["value"], m["unit"], MetricBasis(m["basis"]), tuple(m["evidence_ids"])
        )
        for m in v["traffic_metrics"]
    )
    v["proposed_action_parameters"] = tuple(
        ActionParameter(**p) for p in v["proposed_action_parameters"]
    )
    for name in (
        "affected_flight_ids",
        "faa_event_ids",
        "tradeoffs",
        "assumptions",
        "required_missing_inputs",
    ):
        v[name] = tuple(v[name])
    return FlowCandidate(**v)


def ready_from_dict(value: dict[str, Any]) -> CandidateReady:
    return CandidateReadyMessage(**value).to_candidate()


@dataclass(frozen=True, slots=True)
class WeatherFinding:
    finding_id: str
    envelope: MessageEnvelope
    assessment_round: int
    window_start_utc: datetime
    window_end_utc: datetime
    status: str
    quality: EvidenceQuality
    summary: str
    missing_inputs: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.envelope.sender_agent is not AgentName.WEATHER:
            raise ValueError("Weather findings require the weather role")
        if self.assessment_round not in (1, 2) or self.status not in {
            "impact",
            "no_material_impact",
            "insufficient_data",
        }:
            raise ValueError("Invalid weather round/status")
        for at in (self.window_start_utc, self.window_end_utc):
            _utc_millis(at)
        if (
            self.window_end_utc <= self.window_start_utc
            or not self.finding_id.strip()
            or not self.summary.strip()
        ):
            raise ValueError("Invalid weather finding")
