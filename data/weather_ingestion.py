"""Live AWC METAR/TAF polling, retaining decoded weather in immutable snapshots."""

import asyncio
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from data.awc import AwcClient, AwcError, metar_url
from shared.contracts import (
    MetarEvidence,
    SourceFailure,
    TafEvidence,
    VisibilityBound,
    WeatherRecord,
)
from shared.spacetimedb_adapter import SpacetimeAdapter, SpacetimeError

LOGGER = logging.getLogger("watchtower.weather_ingestion")


def normalized_metar(record: MetarEvidence, tafs: tuple[TafEvidence, ...] = ()) -> WeatherRecord:
    def millis(value: datetime) -> datetime:
        return value.replace(microsecond=value.microsecond // 1000 * 1000)

    tafs = tuple(
        replace(
            taf,
            issued_at_utc=millis(taf.issued_at_utc),
            fetched_at_utc=millis(taf.fetched_at_utc),
            start_utc=millis(taf.start_utc),
            end_utc=millis(taf.end_utc),
            groups=tuple(
                replace(
                    group,
                    start_utc=millis(group.start_utc),
                    end_utc=millis(group.end_utc),
                    transition_end_utc=millis(group.transition_end_utc)
                    if group.transition_end_utc
                    else None,
                )
                for group in taf.groups
            ),
            temperatures=tuple(
                replace(t, valid_at_utc=millis(t.valid_at_utc)) for t in taf.temperatures
            ),
        )
        for taf in tafs
    )
    record = replace(
        record,
        observed_at_utc=record.observed_at_utc.replace(
            microsecond=record.observed_at_utc.microsecond // 1000 * 1000
        ),
        fetched_at_utc=record.fetched_at_utc.replace(
            microsecond=record.fetched_at_utc.microsecond // 1000 * 1000
        ),
    )
    conditions = record.conditions
    return WeatherRecord(
        observed_at_utc=record.observed_at_utc.replace(
            microsecond=record.observed_at_utc.microsecond // 1000 * 1000
        ),
        fetched_at_utc=record.fetched_at_utc.replace(
            microsecond=record.fetched_at_utc.microsecond // 1000 * 1000
        ),
        source_ref=record.source_ref,
        wind_direction_deg=conditions.wind_direction_deg,
        wind_speed_kt=conditions.wind_speed_kt,
        wind_gust_kt=conditions.wind_gust_kt,
        # Scalar storage cannot preserve '<'/'>' qualifiers; leave those unknown.
        visibility_m=conditions.visibility_m
        if conditions.visibility_bound is VisibilityBound.EXACT
        else None,
        ceiling_ft=conditions.ceiling_ft,
        metar_evidence=record,
        taf_evidence=tafs,
    )


class WeatherIngestion:
    def __init__(self, client: AwcClient, adapter: SpacetimeAdapter) -> None:
        self.client, self.adapter = client, adapter
        self.pending: WeatherRecord | None = None
        self.tafs: tuple[TafEvidence, ...] = ()
        self.taf_fetched: datetime | None = None

    async def poll(self) -> None:
        try:
            if self.pending is None:
                if self.taf_fetched is None or datetime.now(UTC) - self.taf_fetched >= timedelta(
                    minutes=5
                ):
                    try:
                        forecast = await self.client.fetch("taf")
                        self.tafs = tuple(
                            record for record in forecast.records if isinstance(record, TafEvidence)
                        )
                        self.taf_fetched = forecast.fetched_at_utc
                    except (AwcError, ValueError):
                        # An unavailable forecast stays unknown; never substitute a fixture.
                        self.tafs, self.taf_fetched = (), None
                        LOGGER.warning("TAF unavailable; current METAR can still be assessed")
                result = await self.client.fetch("metar")
                records = [record for record in result.records if isinstance(record, MetarEvidence)]
                if not records:
                    raise AwcError("no METAR returned")
                self.pending = normalized_metar(
                    max(records, key=lambda record: record.observed_at_utc), self.tafs
                )
            await self.adapter.ingest_weather_record(self.pending)
            self.pending = None
            LOGGER.info("Live KDTW weather committed with %d retained forecasts", len(self.tafs))
        except (AwcError, SpacetimeError, ValueError):
            LOGGER.warning("METAR fetch or commit unavailable; retry remains pending")
            try:
                now = datetime.now(UTC)
                await self.adapter.report_source_failure(
                    SourceFailure(
                        provider="awc",
                        fetched_at_utc=now.replace(microsecond=now.microsecond // 1000 * 1000),
                        source_ref=metar_url(),
                    )
                )
            except (SpacetimeError, ValueError):
                LOGGER.warning("Weather source-health update unavailable")

    async def run(self) -> None:
        while True:
            await asyncio.sleep(60)
            await self.poll()
