"""Reference, provenance and freshness checks on an already-loaded validation packet.

Pure: every input is supplied, including the current time. Missing or stale input is
unknown, never zero, and records from TEST runs or projections never count as live
observations. Agreement between agents that read the same record is one source.
"""

from __future__ import annotations

from collections.abc import Iterable

from agents.validation.packet import (
    EvidenceKind,
    EvidenceRecord,
    Finding,
    Severity,
    ValidationConfig,
    ValidationPacket,
)
from projection.assumptions import SUPPORTED_HORIZONS_MINUTES
from shared.contracts import FlowCandidate, MetricBasis

LIVE = "live"


def _finding(code: str, severity: Severity, detail: str) -> Finding:
    return Finding(code, severity, detail)


def check_references(packet: ValidationPacket) -> tuple[Finding, ...]:
    """The loaded candidate and findings must be the ones this case's messages named."""
    findings: list[Finding] = []
    candidate, ready, opening = packet.candidate, packet.ready, packet.opening
    if candidate is None:
        return (_finding("candidate_missing", Severity.MISSING, "stored candidate not found"),)

    envelopes = (opening, ready.envelope, candidate.envelope)
    if len({e.case_id for e in envelopes}) != 1:
        findings.append(_finding("cross_case_reference", Severity.INTEGRITY, "case IDs differ"))
    if len({e.snapshot_id for e in envelopes}) != 1:
        findings.append(_finding("snapshot_mismatch", Severity.INTEGRITY, "snapshot IDs differ"))
    if len({e.airport_icao for e in envelopes}) != 1:
        findings.append(_finding("airport_mismatch", Severity.INTEGRITY, "airports differ"))

    if candidate.envelope.message_id != ready.candidate_id:
        findings.append(
            _finding(
                "candidate_reference_mismatch",
                Severity.INTEGRITY,
                "loaded candidate is not the one CandidateReady named",
            )
        )
    if candidate.weather_assessment_id != ready.weather_assessment_id:
        findings.append(
            _finding(
                "weather_reference_mismatch",
                Severity.INTEGRITY,
                "candidate and CandidateReady name different weather assessments",
            )
        )
    if candidate.projection_id != ready.projection_id:
        findings.append(
            _finding(
                "projection_reference_mismatch",
                Severity.INTEGRITY,
                "candidate and CandidateReady name different projections",
            )
        )
    # The candidate's own projection is derived from the snapshot, not an input record;
    # check_projection verifies it by snapshot and recomputation instead.
    inputs = set(candidate.envelope.evidence_ids) - {candidate.projection_id}
    if not inputs <= set(opening.evidence_ids):
        findings.append(
            _finding(
                "evidence_outside_case",
                Severity.INTEGRITY,
                "candidate cites evidence that was not in the case snapshot",
            )
        )

    weather = packet.weather
    if weather is not None and (
        weather.assessment_id != ready.weather_assessment_id
        or weather.case_id != opening.case_id
        or weather.snapshot_id != opening.snapshot_id
    ):
        findings.append(
            _finding(
                "weather_finding_mismatch",
                Severity.INTEGRITY,
                "loaded weather assessment belongs to another case, snapshot or reference",
            )
        )
    return tuple(findings)


def cited_evidence_ids(candidate: FlowCandidate) -> tuple[str, ...]:
    """Every evidence ID the candidate relies on, in a stable order."""
    ids: list[str] = list(candidate.envelope.evidence_ids)
    for metric in candidate.traffic_metrics:
        ids.extend(metric.evidence_ids)
    ids.extend(candidate.faa_event_ids)
    if candidate.projection_id is not None:
        ids.append(candidate.projection_id)
    return tuple(dict.fromkeys(ids))


def check_evidence(packet: ValidationPacket, config: ValidationConfig) -> tuple[Finding, ...]:
    """Each cited record exists, is live, belongs to this snapshot and is fresh enough."""
    candidate = packet.candidate
    if candidate is None:
        return ()
    findings: list[Finding] = []
    if not cited_evidence_ids(candidate):
        findings.append(
            _finding("no_evidence", Severity.MISSING, "candidate cites no stored evidence")
        )
    for evidence_id in cited_evidence_ids(candidate):
        record = packet.evidence.get(evidence_id)
        if record is None:
            findings.append(
                _finding("evidence_missing", Severity.MISSING, f"{evidence_id} was not found")
            )
            continue
        findings.extend(_check_record(packet, config, evidence_id, record))
    findings.extend(_check_citation_kinds(packet, candidate))
    return tuple(findings)


def _check_record(
    packet: ValidationPacket, config: ValidationConfig, evidence_id: str, record: EvidenceRecord
) -> Iterable[Finding]:
    if record.evidence_id != evidence_id:
        yield _finding("evidence_id_mismatch", Severity.INTEGRITY, f"{evidence_id} key differs")
    if record.data_mode != LIVE:
        yield _finding(
            "non_live_evidence",
            Severity.INTEGRITY,
            f"{evidence_id} is {record.data_mode} data and cannot support a live advisory",
        )
    if record.airport_icao != packet.opening.airport_icao:
        yield _finding("evidence_airport", Severity.INTEGRITY, f"{evidence_id} is another airport")
    if record.snapshot_id != packet.opening.snapshot_id:
        yield _finding(
            "evidence_snapshot_mismatch",
            Severity.INTEGRITY,
            f"{evidence_id} belongs to another snapshot",
        )
    snapshot_at = packet.snapshot_at_utc
    if record.fetched_at_utc > snapshot_at:
        yield _finding(
            "evidence_after_snapshot",
            Severity.INTEGRITY,
            f"{evidence_id} was fetched after the snapshot cutoff",
        )
        return
    age = snapshot_at - record.fetched_at_utc
    if record.kind is EvidenceKind.TRAFFIC:
        if age > config.position_excluded_after:
            yield _finding("stale_traffic", Severity.MISSING, f"{evidence_id} is too old to use")
        elif age > config.position_stale_after:
            yield _finding(
                "aging_traffic", Severity.LIMITATION, "aircraft positions are older than fresh"
            )
    elif record.kind is EvidenceKind.DEMAND:
        if age > config.demand_excluded_after:
            yield _finding("stale_demand", Severity.MISSING, f"{evidence_id} is too old to use")
    elif record.kind is EvidenceKind.FAA_EVENT:
        if age > config.faa_flagged_after:
            yield _finding(
                "aging_faa_status",
                Severity.LIMITATION,
                "FAA status has not been refreshed successfully within the flag interval",
            )
    elif record.kind is EvidenceKind.WEATHER:
        source_time = record.source_time_utc or record.fetched_at_utc
        if snapshot_at - source_time > config.weather_limited_after:
            yield _finding("aging_weather", Severity.LIMITATION, "weather report is old")
        candidate = packet.candidate
        assert candidate is not None
        if (
            record.valid_until_utc is not None
            and record.valid_until_utc <= candidate.window_start_utc
        ) or (
            record.valid_from_utc is not None and record.valid_from_utc >= candidate.window_end_utc
        ):
            yield _finding(
                "weather_validity_gap",
                Severity.LIMITATION,
                "weather validity does not overlap the advisory window",
            )


def _check_citation_kinds(packet: ValidationPacket, candidate: FlowCandidate) -> Iterable[Finding]:
    """Projections are never observations; FAA and projection references have their kinds."""
    for metric in candidate.traffic_metrics:
        for evidence_id in metric.evidence_ids:
            record = packet.evidence.get(evidence_id)
            if record is None:
                continue
            projected_record = record.kind is EvidenceKind.PROJECTION
            projected_metric = metric.basis is MetricBasis.PROJECTED
            if projected_record and not projected_metric:
                yield _finding(
                    "projection_cited_as_observation",
                    Severity.INTEGRITY,
                    f"{metric.name} presents a projection as {metric.basis.value}",
                )
            elif projected_metric and not projected_record:
                yield _finding(
                    "projected_metric_without_projection",
                    Severity.INTEGRITY,
                    f"{metric.name} is projected but cites a {record.kind.value} record",
                )
    for evidence_id in candidate.faa_event_ids:
        record = packet.evidence.get(evidence_id)
        if record is not None and record.kind is not EvidenceKind.FAA_EVENT:
            yield _finding("faa_event_kind", Severity.INTEGRITY, f"{evidence_id} is not an event")
    if candidate.projection_id is not None:
        record = packet.evidence.get(candidate.projection_id)
        if record is not None and record.kind is not EvidenceKind.PROJECTION:
            yield _finding(
                "projection_kind", Severity.INTEGRITY, "projection reference is another record"
            )


def check_window(packet: ValidationPacket, config: ValidationConfig) -> tuple[Finding, ...]:
    """The window starts at the snapshot, spans a supported horizon and is still current."""
    candidate = packet.candidate
    if candidate is None:
        return ()
    findings: list[Finding] = []
    if (
        abs(candidate.window_start_utc - packet.opening.created_at_utc)
        > config.window_start_tolerance
    ):
        findings.append(
            _finding(
                "window_not_from_snapshot",
                Severity.INTEGRITY,
                "candidate window does not start at the case snapshot",
            )
        )
    span = candidate.window_end_utc - candidate.window_start_utc
    if span.total_seconds() not in {m * 60 for m in SUPPORTED_HORIZONS_MINUTES}:
        findings.append(
            _finding("unsupported_horizon", Severity.INTEGRITY, "window is not 15 or 30 minutes")
        )
    if candidate.window_end_utc <= packet.now_utc:
        findings.append(_finding("window_elapsed", Severity.MISSING, "window has already passed"))
    if candidate.envelope.valid_until_utc <= packet.now_utc:
        findings.append(_finding("candidate_expired", Severity.MISSING, "candidate has expired"))
    return tuple(findings)


def horizon_minutes(candidate: FlowCandidate) -> int:
    return int((candidate.window_end_utc - candidate.window_start_utc).total_seconds() // 60)
