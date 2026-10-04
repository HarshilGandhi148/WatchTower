"""Weather-owned selection and forecast resolution; no live reads or clock access."""

from dataclasses import dataclass, replace
from datetime import datetime

from shared.contracts import MetarEvidence, TafEvidence, WeatherBasis, WeatherConditions


def latest_metar(records: tuple[MetarEvidence, ...], cutoff: datetime) -> MetarEvidence | None:
    usable = [r for r in records if r.observed_at_utc <= cutoff]
    if not usable:
        return None
    latest = max(r.observed_at_utc for r in usable)
    versions = [r for r in usable if r.observed_at_utc == latest]
    if len({r.evidence_id for r in versions}) > 1:
        raise ValueError("Conflicting METAR versions")
    return versions[0]


def latest_taf(
    records: tuple[TafEvidence, ...], cutoff: datetime, start: datetime, end: datetime
) -> TafEvidence | None:
    # Latest issuance first: never fall back to an obsolete covering forecast.
    usable = [r for r in records if r.issued_at_utc <= cutoff]
    if not usable:
        return None
    rank = {"original": 0, "amended": 1, "corrected": 2}
    key = max((r.issued_at_utc, rank[r.amendment]) for r in usable)
    versions = [r for r in usable if (r.issued_at_utc, rank[r.amendment]) == key]
    if len({r.evidence_id for r in versions}) > 1:
        raise ValueError("Conflicting TAF versions")
    result = versions[0]
    return result if result.start_utc < end and result.end_utc > start else None


@dataclass(frozen=True, slots=True)
class ForecastSegment:
    start_utc: datetime
    end_utc: datetime
    conditions: WeatherConditions
    basis: WeatherBasis
    probability_percent: int | None = None


def _overlay(base: WeatherConditions, change: WeatherConditions) -> WeatherConditions:
    # TEMPO/BECMG omit unchanged fields. FM starts a new self-contained forecast.
    return replace(
        change,
        wind_direction_deg=(
            change.wind_direction_deg
            if change.wind_direction_deg is not None or change.wind_variable
            else base.wind_direction_deg
        ),
        wind_variable=change.wind_variable
        if change.wind_direction_deg is not None or change.wind_variable
        else base.wind_variable,
        wind_speed_kt=change.wind_speed_kt
        if change.wind_speed_kt is not None
        else base.wind_speed_kt,
        wind_gust_kt=change.wind_gust_kt if change.wind_speed_kt is not None else base.wind_gust_kt,
        visibility_m=change.visibility_m if change.visibility_m is not None else base.visibility_m,
        visibility_bound=(
            change.visibility_bound if change.visibility_m is not None else base.visibility_bound
        ),
        ceiling_ft=change.ceiling_ft if change.sky_known else base.ceiling_ft,
        sky_known=change.sky_known or base.sky_known,
        clear_skies=change.clear_skies if change.sky_known else base.clear_skies,
        weather_codes=change.weather_codes if change.weather_known else base.weather_codes,
        weather_known=change.weather_known or base.weather_known,
        missing_fields=tuple(f for f in change.missing_fields if f.startswith("invalid_")),
    )


def resolve_forecast(
    taf: TafEvidence, start: datetime, end: datetime
) -> tuple[ForecastSegment, ...]:
    points = sorted(
        {
            start,
            end,
            *(
                t
                for g in taf.groups
                for t in (g.start_utc, g.end_utc, g.transition_end_utc)
                if t is not None and start < t < end
            ),
        }
    )
    results: list[ForecastSegment] = []
    for left, right in zip(points, points[1:], strict=False):
        prevailing: WeatherConditions | None = None
        for group in sorted(taf.groups, key=lambda g: (g.start_utc, g.change)):
            if group.start_utc > left or group.change in {"TEMPO", "PROB"}:
                continue
            if group.change == "BECMG":
                if group.transition_end_utc is not None and left >= group.transition_end_utc:
                    prevailing = _overlay(prevailing or WeatherConditions(), group.conditions)
                continue
            if left < group.end_utc:
                prevailing = group.conditions
            else:
                prevailing = None
        if not taf.start_utc <= left < right <= taf.end_utc:
            continue
        if prevailing is not None:
            results.append(ForecastSegment(left, right, prevailing, WeatherBasis.FORECAST))
        for group in taf.groups:
            if not group.start_utc <= left < group.end_utc:
                continue
            conditional = group.change in {"TEMPO", "PROB"} or group.probability_percent is not None
            transitioning = group.change == "BECMG" and (
                group.transition_end_utc is not None and left < group.transition_end_utc
            )
            if conditional or transitioning:
                results.append(
                    ForecastSegment(
                        left,
                        right,
                        _overlay(prevailing or WeatherConditions(), group.conditions),
                        WeatherBasis.CONDITIONAL if conditional else WeatherBasis.TRANSITION,
                        group.probability_percent,
                    )
                )
    return tuple(results)
