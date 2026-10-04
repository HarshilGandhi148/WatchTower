"""Evidence checks and conservative review suggestions, persisted before display."""

import json
from datetime import datetime, timedelta
from typing import Any

from agents.validation.storage import ValidationStorage
from data.evidence_inputs import stored_feeds
from data.traffic_metrics import demand_windows, nearby_traffic
from shared.contracts import ActionType, FlowCandidate
from shared.orchestration import CandidateReady, ValidationVerdict
from shared.persistence import storage_json, utc
from shared.validation_report import ReportOutcome, ValidationCheck, ValidationReport


def evaluate(
    candidate: FlowCandidate,
    snapshot: dict[str, Any],
    finding: dict[str, Any],
    round_: int,
    now: datetime,
    *,
    projection_verified: bool = False,
) -> ValidationReport:
    e = candidate.envelope
    checks: list[ValidationCheck] = []
    known = set(snapshot["evidence_ids"])
    binding = (
        e.snapshot_id == snapshot["snapshot_id"]
        and e.case_id == snapshot["case_id"]
        and e.airport_icao == snapshot["configuration"]["airport_icao"]
        and candidate.window_start_utc == utc(snapshot["window_start_utc"])
        and candidate.window_end_utc == utc(snapshot["window_end_utc"])
        and finding["envelope"]["snapshot_id"] == e.snapshot_id
        and finding["assessment_round"] == round_
        and finding["finding_id"] == candidate.weather_assessment_id
        and utc(finding["window_start_utc"]) == candidate.window_start_utc
        and utc(finding["window_end_utc"]) == candidate.window_end_utc
        and set(e.evidence_ids) <= known
    )
    checks.append(
        ValidationCheck(
            "binding",
            "pass" if binding else "fail",
            "Case, airport, round and frozen analysis window match."
            if binding
            else "Mismatched case, window or evidence references.",
            tuple(e.evidence_ids),
        )
    )
    fresh = (
        e.created_at_utc
        <= now
        < min(
            e.valid_until_utc,
            utc(snapshot["expires_at_utc"]),
            utc(finding["envelope"]["valid_until_utc"]),
        )
    )
    checks.append(
        ValidationCheck(
            "freshness",
            "pass" if fresh else "fail",
            "Assessment is within its validity window."
            if fresh
            else "Assessment expired or has a future timestamp.",
        )
    )
    feeds = stored_feeds(snapshot)
    traffic = nearby_traffic(
        feeds.adsb,
        candidate.window_start_utc,
        center=(feeds.configuration.latitude_deg, feeds.configuration.longitude_deg),
        radius_nm=feeds.configuration.radius_nm,
    )
    expected = {m.name: m for m in traffic.metrics(feeds.traffic_evidence_id or "unavailable")}
    demand = demand_windows(
        feeds.demand_records,
        feeds.demand_fetched_at_utc,
        candidate.window_start_utc,
        int((candidate.window_end_utc - candidate.window_start_utc).total_seconds() / 60),
    )
    expected.update({m.name: m for m in demand.metrics(feeds.demand_evidence_id or "unavailable")})
    mismatched = False
    unsupported = False
    for metric in candidate.traffic_metrics:
        match = expected.get(metric.name)
        if match is None:
            unsupported = True
        elif metric != match:
            mismatched = True
    checks.append(
        ValidationCheck(
            "metrics",
            "fail" if mismatched or unsupported else "pass",
            "Metric value, unit, basis or evidence differs."
            if mismatched
            else "Some metrics cannot be verified by this validator."
            if unsupported
            else "Metrics match deterministic calculations over copied evidence.",
            tuple(e.evidence_ids),
        )
    )
    weather_ids = set(finding["envelope"]["evidence_ids"])
    weather_available = any(
        row.get("airport_icao", e.airport_icao) == e.airport_icao
        and row.get("evidence_id", f"{e.snapshot_id}:weather:{row['provider']}") in weather_ids
        and timedelta(0)
        <= candidate.window_start_utc - utc(row["observed_at_utc"])
        <= timedelta(minutes=90)
        for row in snapshot["weather"]
    )
    complete = (
        traffic.total is not None and weather_available and finding["status"] != "insufficient_data"
    )
    checks.append(
        ValidationCheck(
            "inputs",
            "pass" if complete else "fail",
            "Traffic and a weather assessment are available for informational review."
            if complete
            else "Obtain fresh traffic and weather evidence before review.",
        )
    )
    conservative = candidate.proposed_action_type in (
        None,
        ActionType.INFORMATION_NOTICE,
        ActionType.REVIEW_FLOW_WINDOW,
    )
    conservative = conservative and not candidate.proposed_action_parameters
    checks.append(
        ValidationCheck(
            "action",
            "pass" if conservative else "fail",
            "Informational review only; no operational instruction."
            if conservative
            else "Operational action requires additional domain validation.",
        )
    )
    checks.append(
        ValidationCheck(
            "projection",
            "not_applicable"
            if not candidate.projection_id
            else "pass"
            if projection_verified
            else "fail",
            "No projection attached."
            if not candidate.projection_id
            else "Stored projection recomputed."
            if projection_verified
            else "Projection verification unavailable.",
            (candidate.projection_id,) if candidate.projection_id else (),
        )
    )
    rejected = not binding or mismatched
    supported = all(c.status != "fail" for c in checks)
    outcome: ReportOutcome = (
        "rejected" if rejected else "supported" if supported else "insufficient_data"
    )
    suggestion = (
        "Review the observed traffic and weather for this flow window; "
        "coordinate any response with the controller."
        if supported
        else "Do not use this candidate: resolve inconsistent evidence and reassess."
        if rejected
        else "Obtain the missing or verifiable inputs before considering a flow response."
    )
    return ValidationReport(
        f"report:{e.message_id}",
        e.message_id,
        e.snapshot_id,
        e.case_id,
        round_,
        now,
        min(e.valid_until_utc, utc(snapshot["expires_at_utc"])),
        outcome,
        tuple(checks),
        suggestion,
        tuple(
            dict.fromkeys(
                (
                    *candidate.required_missing_inputs,
                    *candidate.assumptions,
                    "Informational prototype check; not an operational approval.",
                )
            )
        ),
    )


class ReportValidator:
    def __init__(self, storage: ValidationStorage) -> None:
        self.storage = storage

    async def report(self, ready: CandidateReady, now: datetime) -> dict[str, Any]:
        adapter = self.storage.adapter
        rows = await adapter.read_records(
            "validation_reports", key="report_id", value=f"report:{ready.candidate_id}"
        )
        if rows:
            return dict(json.loads(rows[0]["content_json"]))
        await self.storage.retrieve(ready)  # references resolve; projections recompute
        candidate = await adapter.load_candidate(ready.candidate_id)
        snapshot = await adapter.load_snapshot(ready.envelope.snapshot_id)
        row = await adapter.load_record("agent_findings", "finding_id", ready.weather_assessment_id)
        report = evaluate(
            candidate,
            snapshot,
            json.loads(row["content_json"]),
            ready.assessment_round,
            now,
            projection_verified=bool(candidate.projection_id),
        )
        await adapter.call("store_validation_report", report)
        return dict(json.loads(storage_json(report)))

    async def __call__(self, ready: CandidateReady) -> ValidationVerdict:
        from datetime import UTC

        result = await self.report(ready, datetime.now(UTC))
        # An informational report does not publish an operational advisory.
        return (
            ValidationVerdict.REJECT
            if result["outcome"] == "rejected"
            else ValidationVerdict.INSUFFICIENT_DATA
        )
