"""Evidence checks and conservative review suggestions, persisted before display."""

import json
from dataclasses import replace
from datetime import datetime, timedelta
from typing import Any

from agents.validation.storage import ValidationStorage
from agents.validation.demo import aircraft_recommendations
from data.evidence_inputs import stored_feeds
from data.traffic_metrics import demand_windows, nearby_traffic
from shared.contracts import ActionType, FlowCandidate
from shared.orchestration import CandidateReady, ValidationVerdict
from shared.persistence import storage_json, utc
from shared.validation_report import ReportOutcome, ValidationCheck, ValidationReport
from shared.weather_evidence import metar_from_dict


def weather_context(rows: list[dict[str, Any]], cutoff: datetime) -> str:
    fresh = [
        r
        for r in rows
        if timedelta(0) <= cutoff - utc(r["observed_at_utc"]) <= timedelta(minutes=90)
    ]
    if not fresh:
        return "Current KDTW weather is unavailable."
    row = max(fresh, key=lambda r: utc(r["observed_at_utc"]))
    if "content_json" not in row:
        return "KDTW weather measurements were not retained in this snapshot."
    retained = json.loads(row["content_json"])
    scalar = json.loads(retained[1])
    c = metar_from_dict(retained[2]["metar_evidence"]).conditions if len(retained) == 3 else None
    parts = []
    speed = scalar.get("windSpeedKt")
    direction = scalar.get("windDirectionDeg")
    gust = scalar.get("windGustKt")
    if speed is not None:
        wind = (
            f"wind {direction:g}° at {speed:g} kt"
            if direction is not None
            else f"wind {speed:g} kt"
        )
        if gust is not None:
            wind += f", gusting {gust:g} kt"
        parts.append(wind)
    visibility = c.visibility_m if c else scalar.get("visibilityM")
    if visibility is not None:
        bound = (
            {"exact": "", "greater_than": ">", "less_than": "<"}[c.visibility_bound.value]
            if c
            else ""
        )
        parts.append(f"visibility {bound}{visibility / 1000:g} km")
    ceiling = scalar.get("ceilingFt")
    if ceiling is not None:
        parts.append(f"ceiling {ceiling:g} ft")
    elif c and c.sky_known:
        parts.append("no reported ceiling")
    if c and c.weather_codes:
        parts.append("weather " + ", ".join(c.weather_codes))
    return (
        "KDTW: " + "; ".join(parts) + "." if parts else "KDTW weather measurements are incomplete."
    )


def missing_context(values: tuple[str, ...]) -> str:
    labels = {
        "fresh_METAR": "a fresh KDTW METAR",
        "TAF_window_coverage": "TAF coverage for the analysis window",
        "historical_window_coverage": "weather coverage before the snapshot",
        "conditional_or_transition_uncertainty": "uncertain forecast changes",
        "normalized_METAR_only": "full decoded METAR details",
        "weather_codes": "weather codes",
        "sky": "cloud details",
    }
    return "; ".join(
        dict.fromkeys(
            labels.get(v, labels.get(v.split(":")[-1], v.replace("_", " "))) for v in values
        )
    )


def recommendation_action(
    weather_status: str,
    *,
    traffic_available: bool,
    weather_available: bool,
    gaps: tuple[str, ...],
) -> str:
    """Give a useful next step even when optional inputs cannot be checked."""
    if not traffic_available and not weather_available:
        return "Refresh KDTW weather and aircraft updates, then reassess this window."
    if not weather_available:
        return "Monitor nearby aircraft and refresh KDTW weather for this window."
    traffic_action = (
        "monitor nearby aircraft"
        if traffic_available
        else "restore aircraft updates to assess traffic pressure"
    )
    if weather_status == "impact":
        return f"Review weather-related flow pressure in this window and {traffic_action}."
    if weather_status == "no_material_impact":
        return (
            f"Continue weather monitoring and {traffic_action}; "
            "no material weather impact is identified for this window."
        )
    return (
        f"Review the available KDTW weather and {traffic_action}; "
        f"refresh {missing_context(gaps) if gaps else 'full weather coverage for this window'}."
    )


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
        feeds.cutoff_at_utc or candidate.window_start_utc,
        center=(feeds.configuration.latitude_deg, feeds.configuration.longitude_deg),
        radius_nm=feeds.configuration.radius_nm,
    )
    expected = {m.name: m for m in traffic.metrics(feeds.traffic_evidence_id or "unavailable")}
    demand = demand_windows(
        feeds.demand_records,
        feeds.demand_fetched_at_utc,
        candidate.window_start_utc,
        int((candidate.window_end_utc - candidate.window_start_utc).total_seconds() / 60),
        evidence_time_utc=feeds.cutoff_at_utc,
    )
    expected.update({m.name: m for m in demand.metrics(feeds.demand_evidence_id or "unavailable")})
    mismatched = False
    unsupported: list[str] = []
    for metric in candidate.traffic_metrics:
        match = expected.get(metric.name)
        if match is None:
            unsupported.append(metric.name)
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
        <= (feeds.cutoff_at_utc or candidate.window_start_utc) - utc(row["observed_at_utc"])
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
    traffic_text = (
        f"{traffic.total} nearby aircraft within {traffic.radius_nm:g} NM "
        f"({traffic.airborne} airborne, {traffic.on_ground} on ground)."
        if traffic.total is not None
        else "Nearby traffic is unknown: " + "; ".join(traffic.notes) + "."
    )
    weather_text = weather_context(
        snapshot["weather"], feeds.cutoff_at_utc or candidate.window_start_utc
    )
    gaps = tuple(finding.get("missing_inputs", ()))
    if (
        rejected
        or not fresh
        or not conservative
        or (candidate.projection_id and not projection_verified)
    ):
        action = "Reassess this candidate: " + "; ".join(
            c.reason.rstrip(".") for c in checks if c.status == "fail"
        )
        action += "."
    else:
        action = recommendation_action(
            finding["status"],
            traffic_available=traffic.total is not None,
            weather_available=weather_available,
            gaps=gaps,
        )
    suggestion = f"{action} {traffic_text} {weather_text}"[:2048]
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
                    *gaps,
                    *finding.get("assumptions", ()),
                    *candidate.assumptions,
                    *(
                        ("Supplemental traffic metrics not used: " + ", ".join(unsupported),)
                        if unsupported
                        else ()
                    ),
                    "Informational prototype check; not an operational approval.",
                )
            )
        ),
    )


class ReportValidator:
    def __init__(self, storage: ValidationStorage) -> None:
        self.storage = storage
        self._demo_cases: dict[str, bool] = {}
        self._demo_count = 0

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
        if report.case_id not in self._demo_cases:
            self._demo_cases[report.case_id] = self._demo_count % 2 == 0
            self._demo_count += 1
            if len(self._demo_cases) > 256:
                self._demo_cases.pop(next(iter(self._demo_cases)))
        eligible = (
            not any(
                c.status == "fail"
                for c in report.checks
                if c.name in ("binding", "freshness", "action", "projection")
            )
            and report.outcome != "rejected"
        )
        plans = (
            aircraft_recommendations(stored_feeds(snapshot))
            if eligible and self._demo_cases[report.case_id]
            else ()
        )
        if plans:
            report = replace(
                report,
                recommendation_kind="demo",
                aircraft_recommendations=plans,
                suggestion=(plans[0].recommendation + " " + report.suggestion)[:2048],
                limitations=(
                    *report.limitations,
                    "Demo projections assume current speed and heading continue for two minutes.",
                ),
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
