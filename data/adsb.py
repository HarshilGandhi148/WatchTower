"""ADSB.lol nearby-aircraft adapter: URL building and response normalization.

Pure functions: no network, clock, database or agent access. The poller (integration
owner) performs the HTTP call with bounded timeouts and passes the decoded JSON and
its own successful-fetch time here.

This is a nearby-aircraft feed. Nothing here says an aircraft is a KDTW arrival or
departure. Missing fields stay None (unknown, never zero). Provider response time,
per-aircraft position time and fetch time are kept separate. Callsigns and every
other provider string are untrusted text: they are validated, never interpreted.

Normalized units: altitude in feet, ground speed in knots, track in degrees, position
age in seconds, coordinates WGS84 degrees.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, TypeGuard

SOURCE_ID = "adsb.lol"
DEFAULT_RADIUS_NM = 40
MAX_RADIUS_NM = 250  # documented upper bound of the point query
DEFAULT_MAX_POSITION_AGE_S = 30.0
_MS_EPOCH_THRESHOLD = 1e11  # epoch seconds are ~1.7e9; milliseconds ~1.7e12

_HEX_ID = re.compile(r"^~?[0-9a-f]{6}$")
_CALLSIGN = re.compile(r"^[A-Z0-9]{2,8}$")

# Plausibility limits; values outside are treated as missing and flagged.
_ALTITUDE_FT = (-2000.0, 100000.0)
_GROUND_SPEED_KT = (0.0, 2000.0)


class AdsbParseError(ValueError):
    """The response is not a usable ADSB.lol point payload (distinct from an empty one)."""


@dataclass(frozen=True, slots=True)
class AdsbObservation:
    """One aircraft position. ``None`` means the provider did not supply a valid value."""

    aircraft_id: str
    callsign: str | None
    latitude_deg: float | None
    longitude_deg: float | None
    altitude_ft: float | None
    on_ground: bool | None
    ground_speed_kt: float | None
    track_deg: float | None
    position_age_s: float | None
    observed_at_utc: datetime | None
    provider_time_utc: datetime | None
    fetched_at_utc: datetime
    missing_fields: tuple[str, ...]
    quality_flags: tuple[str, ...]

    @property
    def has_position(self) -> bool:
        return self.latitude_deg is not None and self.longitude_deg is not None

    def is_stale(self, max_age_s: float = DEFAULT_MAX_POSITION_AGE_S) -> bool:
        """Unknown age counts as stale: it must not enter current metrics."""
        return self.position_age_s is None or self.position_age_s > max_age_s


@dataclass(frozen=True, slots=True)
class AdsbSnapshot:
    """All aircraft from one successful response, with accounting for dropped records."""

    source_id: str
    provider_time_utc: datetime | None
    fetched_at_utc: datetime
    observations: tuple[AdsbObservation, ...]
    reported_total: int | None
    rejected_records: int
    duplicates_dropped: int


def point_url(
    base_url: str, latitude_deg: float, longitude_deg: float, radius_nm: int = DEFAULT_RADIUS_NM
) -> str:
    """Build the documented point query URL, validating the center and radius."""
    if not isinstance(base_url, str) or not base_url.startswith("https://"):
        raise ValueError("base_url must be an https URL")
    if not _finite(latitude_deg) or not -90 <= latitude_deg <= 90:
        raise ValueError("latitude must be within [-90, 90]")
    if not _finite(longitude_deg) or not -180 <= longitude_deg <= 180:
        raise ValueError("longitude must be within [-180, 180]")
    if type(radius_nm) is not int or not 1 <= radius_nm <= MAX_RADIUS_NM:
        raise ValueError(f"radius_nm must be an integer in [1, {MAX_RADIUS_NM}]")
    return f"{base_url.rstrip('/')}/v2/point/{latitude_deg:.4f}/{longitude_deg:.4f}/{radius_nm}"


def parse_point_response(payload: object, fetched_at_utc: datetime) -> AdsbSnapshot:
    """Normalize a decoded point response.

    An empty ``ac`` list is a valid snapshot with no aircraft. A missing or non-list
    ``ac`` is a malformed response and raises, so it can be recorded as a failure
    rather than as zero traffic.
    """
    if fetched_at_utc.utcoffset() != timedelta(0):
        raise ValueError("fetched_at_utc must be an aware UTC datetime")
    if not isinstance(payload, dict):
        raise AdsbParseError("response is not a JSON object")
    records = payload.get("ac")
    if not isinstance(records, list):
        raise AdsbParseError("response has no aircraft list")

    provider_time = _provider_time(payload.get("now"))
    reported = payload.get("total")
    reported_total = reported if type(reported) is int and reported >= 0 else None

    best: dict[str, AdsbObservation] = {}
    rejected = duplicates = 0
    for record in records:
        observation = _parse_record(record, provider_time, fetched_at_utc)
        if observation is None:
            rejected += 1
            continue
        previous = best.get(observation.aircraft_id)
        if previous is None:
            best[observation.aircraft_id] = observation
            continue
        duplicates += 1
        if _fresher(observation, previous):
            best[observation.aircraft_id] = observation
    return AdsbSnapshot(
        source_id=SOURCE_ID,
        provider_time_utc=provider_time,
        fetched_at_utc=fetched_at_utc,
        observations=tuple(best.values()),
        reported_total=reported_total,
        rejected_records=rejected,
        duplicates_dropped=duplicates,
    )


def _finite(value: object) -> TypeGuard[int | float]:
    return (type(value) is int or type(value) is float) and math.isfinite(value)


def _number(
    record: dict[str, Any], key: str, bounds: tuple[float, float] | None, flags: list[str]
) -> float | None:
    value = record.get(key)
    if value is None:
        return None
    if not _finite(value) or (bounds is not None and not bounds[0] <= value <= bounds[1]):
        flags.append(f"invalid_{key}")
        return None
    return float(value)


def _provider_time(value: object) -> datetime | None:
    if not _finite(value) or value <= 0:
        return None
    seconds = value / 1000 if value >= _MS_EPOCH_THRESHOLD else value
    try:
        return datetime.fromtimestamp(seconds, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _fresher(candidate: AdsbObservation, current: AdsbObservation) -> bool:
    if candidate.position_age_s is None:
        return False
    return current.position_age_s is None or candidate.position_age_s < current.position_age_s


def _parse_record(
    record: object, provider_time: datetime | None, fetched_at_utc: datetime
) -> AdsbObservation | None:
    if not isinstance(record, dict):
        return None
    raw_id = record.get("hex")
    if not isinstance(raw_id, str) or not _HEX_ID.match(raw_id.strip().lower()):
        return None
    aircraft_id = raw_id.strip().lower()
    flags: list[str] = []
    if aircraft_id.startswith("~"):
        flags.append("non_icao_address")

    raw_callsign = record.get("flight")
    callsign: str | None = None
    if isinstance(raw_callsign, str) and raw_callsign.strip():
        candidate = raw_callsign.strip().upper()
        if _CALLSIGN.match(candidate):
            callsign = candidate
        else:
            flags.append("callsign_rejected")

    latitude = _number(record, "lat", (-90.0, 90.0), flags)
    longitude = _number(record, "lon", (-180.0, 180.0), flags)
    if (latitude is None) != (longitude is None):
        latitude = longitude = None
        flags.append("incomplete_position")

    raw_altitude = record.get("alt_baro")
    on_ground: bool | None = None
    altitude: float | None = None
    if isinstance(raw_altitude, str) and raw_altitude.strip().lower() == "ground":
        on_ground = True
    elif raw_altitude is not None:
        altitude = _number(record, "alt_baro", _ALTITUDE_FT, flags)
        on_ground = False if altitude is not None else None

    speed = _number(record, "gs", _GROUND_SPEED_KT, flags)
    track = _number(record, "track", (0.0, 360.0), flags)
    age = _number(record, "seen_pos", (0.0, 86400.0), flags)

    observed_at: datetime | None = None
    if age is not None and provider_time is not None:
        observed_at = provider_time - timedelta(seconds=age)

    # A ground aircraft legitimately has no numeric altitude, so it is not "missing".
    unknown = {
        "callsign": callsign is None,
        "position": latitude is None,
        "altitude": altitude is None and on_ground is not True,
        "ground_speed": speed is None,
        "track": track is None,
        "position_age": age is None,
    }
    missing = tuple(name for name, is_unknown in unknown.items() if is_unknown)
    if age is None and latitude is not None:
        flags.append("position_age_unknown")
    return AdsbObservation(
        aircraft_id=aircraft_id,
        callsign=callsign,
        latitude_deg=latitude,
        longitude_deg=longitude,
        altitude_ft=altitude,
        on_ground=on_ground,
        ground_speed_kt=speed,
        track_deg=track,
        position_age_s=age,
        observed_at_utc=observed_at,
        provider_time_utc=provider_time,
        fetched_at_utc=fetched_at_utc,
        missing_fields=missing,
        quality_flags=tuple(flags),
    )
