"""Pure fixed-snapshot weather assessment. Thresholds are project rules, not ATC standards."""

import hashlib
import json
import math
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta

from agents.weather.parsing import latest_metar, latest_taf, resolve_forecast
from shared.contracts import (
    AgentName,
    EvidenceQuality,
    VisibilityBound,
    WeatherAssessment,
    WeatherAssessmentRequest,
    WeatherBasis,
    WeatherConditions,
    WeatherFact,
    WeatherHazard,
    WeatherImplication,
    WeatherSnapshot,
    WeatherStatus,
    weather_utc,
)

PROMPT_VERSION = "weather-explanations-v1"
LIMITATIONS = (
    "Project thresholds are demo review rules, not official airport operating limits.",
    "Coverage is KDTW METAR/TAF only; regional hazards are not assessed.",
    "Weather does not establish runway closure, aircraft readiness or official capacity.",
)
IMPLICATIONS = {
    "wind": "Wind may affect flow coordination; assess local operating constraints.",
    "gusts": "Gusts may affect flow coordination; assess local operating constraints.",
    "visibility": "Reduced visibility may warrant review of the affected flow window.",
    "ceiling": "Low ceiling may warrant review of the affected flow window.",
    "thunderstorm": "Thunderstorm signals may affect the forecast flow window.",
    "freezing_precipitation": (
        "Freezing precipitation is an icing-related concern; "
        "runway condition and airborne icing are not established."
    ),
    "snow": "Snow may affect flow; surface condition is not established by this report.",
    "precipitation": "Precipitation may warrant attention to local conditions.",
    "wind_change": "A wind-speed change may warrant review of the affected flow window.",
    "gust_change": "A gust change may warrant review of the affected flow window.",
    "direction_change": (
        "A wind-direction change may warrant review of local operating constraints."
    ),
}


@dataclass(frozen=True, slots=True)
class WeatherRules:
    wind_kt: float = 20.0
    gust_kt: float = 30.0
    visibility_m: float = 3 * 1609.344
    ceiling_ft: float = 1000.0
    wind_change_kt: float = 10.0
    direction_change_deg: float = 45.0
    metar_max_age_minutes: float = 90.0
    version: str = "weather-rules-v1"

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("Rules version is required")
        for name, value in asdict(self).items():
            if name == "version":
                continue
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or (not math.isfinite(value) or value <= 0)
            ):
                raise ValueError("Weather thresholds must be positive finite numbers")
        if self.direction_change_deg > 180:
            raise ValueError("Direction change threshold exceeds 180 degrees")


DEFAULT_RULES = WeatherRules()


def missing_conditions(c: WeatherConditions) -> tuple[str, ...]:
    missing = list(c.missing_fields)
    if c.wind_speed_kt is None:
        missing.append("wind_speed")
    if c.wind_direction_deg is None and not c.wind_variable and c.wind_speed_kt != 0:
        missing.append("wind_direction")
    if c.visibility_m is None:
        missing.append("visibility")
    if not c.sky_known:
        missing.append("sky")
    if not c.weather_known:
        missing.append("weather_codes")
    return tuple(dict.fromkeys(missing))


def hazard_kinds(c: WeatherConditions, rules: WeatherRules) -> tuple[str, ...]:
    kinds: list[str] = []
    if c.wind_speed_kt is not None and c.wind_speed_kt >= rules.wind_kt:
        kinds.append("wind")
    if c.wind_gust_kt is not None and c.wind_gust_kt >= rules.gust_kt:
        kinds.append("gusts")
    if c.visibility_m is not None and (
        c.visibility_bound is not VisibilityBound.GREATER_THAN
        and c.visibility_m < rules.visibility_m
        or c.visibility_bound is VisibilityBound.LESS_THAN
        and c.visibility_m <= rules.visibility_m
    ):
        kinds.append("visibility")
    if c.ceiling_ft is not None and c.ceiling_ft < rules.ceiling_ft:
        kinds.append("ceiling")
    for code in c.weather_codes:
        if "TS" in code:
            kinds.append("thunderstorm")
        if "FZ" in code and any(p in code for p in ("RA", "DZ", "UP")):
            kinds.append("freezing_precipitation")
        if "SN" in code or "SG" in code:
            kinds.append("snow")
        if "RA" in code or "DZ" in code:
            kinds.append("precipitation")
    return tuple(dict.fromkeys(kinds))


def _visibility_uncertain(c: WeatherConditions, rules: WeatherRules) -> bool:
    if c.visibility_m is None:
        return False
    return (
        c.visibility_bound is VisibilityBound.GREATER_THAN
        and c.visibility_m < rules.visibility_m
        or c.visibility_bound is VisibilityBound.LESS_THAN
        and c.visibility_m > rules.visibility_m
    )


def render_summary(
    assessment: WeatherAssessment, ordered: tuple[WeatherImplication, ...] | None = None
) -> str:
    prefix = {
        WeatherStatus.IMPACT: "Supported weather concerns overlap the requested KDTW window.",
        WeatherStatus.NO_MATERIAL_IMPACT: (
            "No material weather impact under the project rules in the covered KDTW window."
        ),
        WeatherStatus.INSUFFICIENT_DATA: (
            "Insufficient weather evidence for the requested KDTW window."
        ),
    }[assessment.status]
    sentences = [prefix]
    for fact in assessment.weather_facts:
        bound = {
            VisibilityBound.EXACT: "",
            VisibilityBound.GREATER_THAN: ">",
            VisibilityBound.LESS_THAN: "<",
        }[fact.bound]
        sentences.append(
            f"{fact.basis.value} {fact.name}: {bound}{fact.value:g} {fact.unit} "
            f"[{', '.join((fact.evidence_id, *fact.related_evidence_ids))}] "
            f"({fact.start_utc.isoformat()} to {fact.end_utc.isoformat()})."
        )
    sentences.extend(
        i.text for i in (ordered if ordered is not None else assessment.operational_implications)
    )
    if assessment.missing_inputs:
        sentences.append("Missing or uncertain: " + ", ".join(assessment.missing_inputs) + ".")
    return " ".join(sentences)


def assess_weather(
    request: WeatherAssessmentRequest,
    snapshot: WeatherSnapshot,
    now_utc: datetime,
    rules: WeatherRules = DEFAULT_RULES,
) -> WeatherAssessment:
    weather_utc(now_utc)
    e = request.envelope
    if e.valid_until_utc <= now_utc or e.created_at_utc > now_utc:
        raise ValueError("Weather request expired or created in the future")
    if (
        snapshot.snapshot_id != e.snapshot_id
        or snapshot.airport_icao != e.airport_icao
        or set(snapshot.evidence_ids) != set(e.evidence_ids)
        or snapshot.cutoff_utc > now_utc
    ):
        raise ValueError("Request does not match fixed weather snapshot")
    start, end = request.window_start_utc, request.window_end_utc
    missing: list[str] = []
    facts: list[WeatherFact] = []
    hazards: list[WeatherHazard] = []

    def add(
        c: WeatherConditions,
        evidence: str,
        left: datetime,
        right: datetime,
        basis: WeatherBasis,
        probability: int | None = None,
    ) -> None:
        for name in missing_conditions(c):
            missing.append(f"{basis.value}:{name}")
        if _visibility_uncertain(c, rules):
            missing.append(f"{basis.value}:visibility_bound")
        for name, unit in (
            ("wind_direction_deg", "deg"),
            ("wind_speed_kt", "kt"),
            ("wind_gust_kt", "kt"),
            ("visibility_m", "m"),
            ("ceiling_ft", "ft"),
            ("temperature_c", "C"),
            ("dewpoint_c", "C"),
        ):
            value = getattr(c, name)
            if value is not None:
                facts.append(
                    WeatherFact(
                        name,
                        value,
                        unit,
                        evidence,
                        basis,
                        left,
                        right,
                        c.visibility_bound if name == "visibility_m" else VisibilityBound.EXACT,
                    )
                )
        for kind in hazard_kinds(c, rules):
            hazards.append(WeatherHazard(kind, "KDTW", left, right, basis, evidence, probability))

    metar = latest_metar(snapshot.metars, snapshot.cutoff_utc)
    # METAR is a current observation, not evidence of future or earlier conditions.
    if start <= now_utc < end:
        if metar is None or now_utc - metar.observed_at_utc > timedelta(
            minutes=rules.metar_max_age_minutes
        ):
            missing.append("fresh_METAR")
        else:
            add(
                metar.conditions,
                metar.evidence_id,
                max(start, now_utc),
                min(end, now_utc + timedelta(microseconds=1)),
                WeatherBasis.OBSERVED,
            )
            previous = latest_metar(
                tuple(r for r in snapshot.metars if r.observed_at_utc < metar.observed_at_utc),
                snapshot.cutoff_utc,
            )
            if previous is not None and (
                metar.observed_at_utc - previous.observed_at_utc
                <= timedelta(minutes=rules.metar_max_age_minutes)
            ):
                for field_name, kind, threshold, unit in (
                    ("wind_speed_kt", "wind_change", rules.wind_change_kt, "kt"),
                    ("wind_gust_kt", "gust_change", rules.wind_change_kt, "kt"),
                    ("wind_direction_deg", "direction_change", rules.direction_change_deg, "deg"),
                ):
                    old, new = (
                        getattr(previous.conditions, field_name),
                        getattr(metar.conditions, field_name),
                    )
                    if old is None or new is None:
                        continue
                    delta = abs(new - old)
                    if unit == "deg":
                        delta = min(delta % 360, 360 - delta % 360)
                    if delta >= threshold:
                        left, right = (
                            max(start, now_utc),
                            min(end, now_utc + timedelta(microseconds=1)),
                        )
                        facts.append(
                            WeatherFact(
                                kind,
                                delta,
                                unit,
                                metar.evidence_id,
                                WeatherBasis.OBSERVED,
                                left,
                                right,
                                related_evidence_ids=(previous.evidence_id,),
                            )
                        )
                        hazards.append(
                            WeatherHazard(
                                kind,
                                "KDTW",
                                left,
                                right,
                                WeatherBasis.OBSERVED,
                                metar.evidence_id,
                                related_evidence_ids=(previous.evidence_id,),
                            )
                        )
    if start < snapshot.cutoff_utc:
        missing.append("historical_window_coverage")
    forecast_start = max(start, snapshot.cutoff_utc)
    if forecast_start < end:
        taf = latest_taf(snapshot.tafs, snapshot.cutoff_utc, forecast_start, end)
        segments = resolve_forecast(taf, forecast_start, end) if taf is not None else ()
        cursor = forecast_start
        for segment in segments:
            if segment.basis is WeatherBasis.FORECAST:
                if segment.start_utc > cursor:
                    missing.append("TAF_window_coverage")
                cursor = max(cursor, segment.end_utc)
            add(
                segment.conditions,
                taf.evidence_id if taf else "",
                segment.start_utc,
                segment.end_utc,
                segment.basis,
                segment.probability_percent,
            )
        if taf is not None:
            for temperature in taf.temperatures:
                if forecast_start <= temperature.valid_at_utc < end:
                    facts.append(
                        WeatherFact(
                            "temperature_" + temperature.kind.lower(),
                            temperature.temperature_c,
                            "C",
                            taf.evidence_id,
                            WeatherBasis.FORECAST,
                            temperature.valid_at_utc,
                            min(end, temperature.valid_at_utc + timedelta(microseconds=1)),
                        )
                    )
        if cursor < end:
            missing.append("TAF_window_coverage")
        if any(s.basis in {WeatherBasis.CONDITIONAL, WeatherBasis.TRANSITION} for s in segments):
            missing.append("conditional_or_transition_uncertainty")
    missing_tuple = tuple(dict.fromkeys(missing))
    status = (
        WeatherStatus.IMPACT
        if hazards
        else WeatherStatus.INSUFFICIENT_DATA
        if missing_tuple
        else WeatherStatus.NO_MATERIAL_IMPACT
    )
    quality = EvidenceQuality.LIMITED if missing_tuple else EvidenceQuality.ADEQUATE
    if not facts and not hazards:
        quality = EvidenceQuality.UNAVAILABLE
    implications: list[WeatherImplication] = []
    for kind in dict.fromkeys(h.kind for h in hazards):
        relevant = [h for h in hazards if h.kind == kind]
        bases = ", ".join(dict.fromkeys(h.basis.value for h in relevant))
        implications.append(
            WeatherImplication(
                kind,
                f"{bases}: {IMPLICATIONS[kind]}",
                tuple(
                    dict.fromkeys(
                        evidence
                        for h in relevant
                        for evidence in (h.evidence_id, *h.related_evidence_ids)
                    )
                ),
            )
        )
    identity = json.dumps(
        [
            e.message_id,
            e.case_id,
            e.snapshot_id,
            request.attempt,
            asdict(rules),
            start.isoformat(),
            end.isoformat(),
        ],
        sort_keys=True,
    )
    assessment_id = "weather-" + hashlib.sha256(identity.encode()).hexdigest()
    envelope = replace(
        e,
        message_id=assessment_id + ":reply",
        sender_agent=AgentName.WEATHER,
        created_at_utc=now_utc,
        reply_to=e.message_id,
    )
    result = WeatherAssessment(
        assessment_id=assessment_id,
        envelope=envelope,
        window_start_utc=start,
        window_end_utc=end,
        status=status,
        quality=quality,
        hazards=tuple(hazards),
        weather_facts=tuple(facts),
        operational_implications=tuple(implications),
        missing_inputs=missing_tuple,
        assumptions=LIMITATIONS,
        summary="pending deterministic rendering",
        rules_version=rules.version,
        prompt_version=PROMPT_VERSION,
    )
    return replace(result, summary=render_summary(result))
