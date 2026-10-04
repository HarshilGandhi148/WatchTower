"""Synthetic normalized observations exclusively for isolated TEST databases."""

from datetime import datetime

from shared.contracts import AircraftBatch, AircraftObservation, WeatherRecord


def weather(at: datetime, speed: float = 10) -> WeatherRecord:
    return WeatherRecord(
        observed_at_utc=at,
        fetched_at_utc=at,
        source_ref="https://aviationweather.gov/test-reference",
        wind_speed_kt=speed,
    )


def aircraft(at: datetime) -> AircraftBatch:
    return AircraftBatch(
        fetched_at_utc=at,
        source_ref="https://api.adsb.lol/test-reference",
        observations=(
            AircraftObservation(
                aircraft_id="test-plane",
                observed_at_utc=at,
                latitude_deg=42.2,
                longitude_deg=-83.3,
                position_age_s=0,
                on_ground=True,
            ),
        ),
    )
