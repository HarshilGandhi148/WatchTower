"""Bridge immutable database METAR snapshots to the weather service and stored findings."""

import json
from dataclasses import replace

from shared.contracts import (
    MetarEvidence,
    TafEvidence,
    WeatherAssessment,
    WeatherConditions,
    WeatherSnapshot,
)
from shared.persistence import WeatherFinding, utc
from shared.spacetimedb_adapter import SpacetimeAdapter
from shared.weather_evidence import metar_from_dict, taf_from_dict


class WeatherStorage:
    def __init__(self, adapter: SpacetimeAdapter) -> None:
        self.adapter = adapter

    async def load_snapshot(self, snapshot_id: str) -> WeatherSnapshot:
        snapshot = await self.adapter.load_snapshot(snapshot_id)
        metars = []
        tafs: list[TafEvidence] = []
        for record in snapshot["weather"]:
            source = json.loads(record["content_json"])
            if (
                not isinstance(source, list)
                or len(source) not in (2, 3)
                or source[0] != record["source_ref"]
                or not isinstance(source[1], str)
            ):
                raise ValueError("Invalid retained weather content")
            value = json.loads(source[1])
            if not isinstance(value, dict):
                raise ValueError("Invalid retained weather measurements")
            parent_id = f"{snapshot_id}:weather:{record['provider']}"
            if len(source) == 3:
                retained = source[2]
                if retained.get("metar_evidence") is not None:
                    metar = metar_from_dict(retained["metar_evidence"])
                    if (
                        metar.source_ref != record["source_ref"]
                        or metar.observed_at_utc != utc(record["observed_at_utc"])
                        or metar.fetched_at_utc != utc(record["fetched_at_utc"])
                    ):
                        raise ValueError("Retained METAR does not match snapshot")
                    metars.append(replace(metar, evidence_id=parent_id))
                    tafs.extend(
                        replace(taf_from_dict(forecast), evidence_id=f"{parent_id}:taf:{index}")
                        for index, forecast in enumerate(retained["taf_evidence"])
                    )
                    continue
            # The normalized storage contract retains scalar observations only.
            # Never reconstruct clear skies, precipitation codes or TAF coverage.
            metars.append(
                MetarEvidence(
                    evidence_id=f"{snapshot_id}:weather:{record['provider']}",
                    observed_at_utc=utc(record["observed_at_utc"]),
                    fetched_at_utc=utc(record["fetched_at_utc"]),
                    source_ref=record["source_ref"],
                    raw_text="",
                    conditions=WeatherConditions(
                        wind_direction_deg=value.get("windDirectionDeg"),
                        wind_speed_kt=value.get("windSpeedKt"),
                        wind_gust_kt=value.get("windGustKt"),
                        visibility_m=value.get("visibilityM"),
                        ceiling_ft=value.get("ceilingFt"),
                        sky_known=value.get("ceilingFt") is not None,
                        missing_fields=("normalized_METAR_only",),
                    ),
                )
            )
        return WeatherSnapshot(
            snapshot_id=snapshot_id,
            cutoff_utc=utc(snapshot["cutoff_at_utc"]),
            evidence_ids=tuple(snapshot["evidence_ids"]),
            metars=tuple(metars),
            tafs=tuple(tafs),
        )

    async def persist_assessment(self, assessment: WeatherAssessment) -> None:
        prefix = f"{assessment.envelope.case_id}:weather-request:"
        reply_to = assessment.envelope.reply_to or ""
        if reply_to not in {prefix + "1", prefix + "2"}:
            raise ValueError("Assessment does not answer a weather request")
        await self.adapter.save_weather(
            WeatherFinding(
                finding_id=assessment.assessment_id,
                envelope=assessment.envelope,
                assessment_round=int(reply_to.removeprefix(prefix)),
                window_start_utc=assessment.window_start_utc,
                window_end_utc=assessment.window_end_utc,
                status=assessment.status.value,
                quality=assessment.quality,
                summary=assessment.summary[:2000],
                missing_inputs=assessment.missing_inputs,
                assumptions=assessment.assumptions,
            )
        )
