"""Decode retained, typed AWC evidence without reparsing or inventing source fields."""

from typing import Any

from shared.contracts import (
    ForecastGroup,
    ForecastTemperature,
    MetarEvidence,
    TafEvidence,
    VisibilityBound,
    WeatherConditions,
)
from shared.persistence import utc


def conditions_from_dict(value: dict[str, Any]) -> WeatherConditions:
    fields = dict(value)
    fields["visibility_bound"] = VisibilityBound(fields["visibility_bound"])
    for name in ("weather_codes", "missing_fields"):
        fields[name] = tuple(fields[name])
    return WeatherConditions(**fields)


def metar_from_dict(value: dict[str, Any]) -> MetarEvidence:
    fields = dict(value)
    for name in ("observed_at_utc", "fetched_at_utc"):
        fields[name] = utc(fields[name])
    fields["conditions"] = conditions_from_dict(fields["conditions"])
    return MetarEvidence(**fields)


def taf_from_dict(value: dict[str, Any]) -> TafEvidence:
    fields = dict(value)
    for name in ("issued_at_utc", "fetched_at_utc", "start_utc", "end_utc"):
        fields[name] = utc(fields[name])
    groups = []
    for value in fields["groups"]:
        group = dict(value)
        for name in ("start_utc", "end_utc", "transition_end_utc"):
            if group[name] is not None:
                group[name] = utc(group[name])
        group["conditions"] = conditions_from_dict(group["conditions"])
        groups.append(ForecastGroup(**group))
    fields["groups"] = tuple(groups)
    fields["temperatures"] = tuple(
        ForecastTemperature(utc(value["valid_at_utc"]), value["temperature_c"], value["kind"])
        for value in fields["temperatures"]
    )
    return TafEvidence(**fields)
