"""Deterministic congestion metrics over fixed inputs.

Nearby traffic (ADS-B) and airport-associated demand (flight records) are separate
results and are never summed or substituted for each other: an aircraft near KDTW is
not a KDTW arrival or departure. Stale, missing or unverifiable input yields ``None``
(unknown), never zero. All functions are pure; the caller supplies ``now_utc``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from data.adsb import AdsbSnapshot
from data.flightaware import Association, DemandRecord, FlightDirection, TimeKind
from shared.contracts import EvidenceQuality, MetricBasis, TrafficMetric

EARTH_RADIUS_NM = 3440.065
WINDOW_MINUTES = 5
# Approximate KDTW reference point. Confirm against airport metadata before the demo.
KDTW_REFERENCE_POINT = (42.2122, -83.3533)
DEFAULT_RADIUS_NM = 40.0
DEFAULT_MAX_POSITION_AGE_S = 30.0
DEFAULT_MAX_SNAPSHOT_AGE = timedelta(seconds=60)
DEFAULT_MAX_DEMAND_AGE = timedelta(minutes=5)
MIN_USABLE_FRACTION = 0.5


def distance_nm(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in nautical miles."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi, dlambda = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlambda / 2) ** 2
    return 2 * EARTH_RADIUS_NM * math.asin(min(1.0, math.sqrt(a)))


@dataclass(frozen=True, slots=True)
class NearbyTraffic:
    """Counts of fresh, positioned aircraft inside the radius; ``None`` when unknown."""

    total: int | None
    airborne: int | None
    on_ground: int | None
    unknown_ground_state: int | None
    excluded_stale: int
    excluded_no_position: int
    outside_radius: int
    radius_nm: float
    quality: EvidenceQuality
    notes: tuple[str, ...] = ()

    def metrics(self, evidence_id: str) -> tuple[TrafficMetric, ...]:
        """Contract metrics. Unknown counts are omitted, not reported as zero."""
        values = (
            ("nearby_aircraft_total", self.total),
            ("nearby_aircraft_airborne", self.airborne),
            ("nearby_aircraft_on_ground", self.on_ground),
        )
        return tuple(
            TrafficMetric(name, value, "aircraft", MetricBasis.OBSERVED, (evidence_id,))
            for name, value in values
            if value is not None
        )


def nearby_traffic(
    snapshot: AdsbSnapshot | None,
    now_utc: datetime,
    *,
    center: tuple[float, float] = KDTW_REFERENCE_POINT,
    radius_nm: float = DEFAULT_RADIUS_NM,
    max_position_age_s: float = DEFAULT_MAX_POSITION_AGE_S,
    max_snapshot_age: timedelta = DEFAULT_MAX_SNAPSHOT_AGE,
) -> NearbyTraffic:
    """Count nearby aircraft from one fixed ADS-B snapshot.

    A missing or stale snapshot is unavailable. An empty but fresh snapshot gives a
    count of zero with LIMITED quality, because quiet airspace and absent coverage
    look identical and coverage has not been measured.
    """
    _require_utc(now_utc)
    if radius_nm <= 0:
        raise ValueError("radius_nm must be positive")

    def unavailable(note: str) -> NearbyTraffic:
        return NearbyTraffic(
            None, None, None, None, 0, 0, 0, radius_nm, EvidenceQuality.UNAVAILABLE, (note,)
        )

    if snapshot is None:
        return unavailable("no ADS-B snapshot")
    if now_utc - snapshot.fetched_at_utc > max_snapshot_age:
        return unavailable("ADS-B snapshot is stale")

    stale = no_position = outside = 0
    airborne = ground = unknown = 0
    for obs in snapshot.observations:
        if not obs.has_position:
            no_position += 1
        elif obs.is_stale(max_position_age_s):
            stale += 1
        else:
            assert obs.latitude_deg is not None and obs.longitude_deg is not None
            if distance_nm(*center, obs.latitude_deg, obs.longitude_deg) > radius_nm:
                outside += 1
            elif obs.on_ground is True:
                ground += 1
            elif obs.on_ground is False:
                airborne += 1
            else:
                unknown += 1

    inside = airborne + ground + unknown
    received = len(snapshot.observations)
    notes: list[str] = []
    if received == 0:
        quality = EvidenceQuality.LIMITED
        notes.append("empty response: quiet airspace and missing coverage are indistinguishable")
    elif inside + outside == 0:
        return unavailable("no aircraft had a fresh position")
    elif (inside + outside) / received < MIN_USABLE_FRACTION:
        quality = EvidenceQuality.LIMITED
        notes.append("fewer than half of the reported aircraft had a fresh position")
    else:
        quality = EvidenceQuality.ADEQUATE
    if unknown:
        notes.append("some aircraft have unknown ground state")
    return NearbyTraffic(
        inside, airborne, ground, unknown, stale, no_position, outside, radius_nm, quality,
        tuple(notes),
    )  # fmt: skip


def traffic_change(current: NearbyTraffic, previous: NearbyTraffic | None) -> int | None:
    """Change in nearby total; ``None`` unless both counts are known and comparable."""
    if previous is None or current.total is None or previous.total is None:
        return None
    if current.radius_nm != previous.radius_nm:
        return None
    return current.total - previous.total


@dataclass(frozen=True, slots=True)
class DemandWindow:
    """Five-minute demand from flight records, confirmed and inferred kept apart."""

    start_utc: datetime
    end_utc: datetime
    departures_confirmed: int
    departures_inferred: int
    arrivals_confirmed: int
    arrivals_inferred: int


@dataclass(frozen=True, slots=True)
class DemandWindows:
    """``windows`` is ``None`` when demand is unavailable; it is never an empty stand-in."""

    windows: tuple[DemandWindow, ...] | None
    unplaced_records: int = 0
    assumptions: tuple[str, ...] = ()
    unavailable_reason: str | None = None
    quality: EvidenceQuality = EvidenceQuality.UNAVAILABLE
    notes: tuple[str, ...] = field(default=())

    def metrics(self, evidence_id: str) -> tuple[TrafficMetric, ...]:
        if self.windows is None:
            return ()
        out: list[TrafficMetric] = []
        for name, attr, basis in (
            ("departure_demand_confirmed", "departures_confirmed", MetricBasis.ESTIMATED),
            ("departure_demand_inferred", "departures_inferred", MetricBasis.INFERRED),
            ("arrival_demand_confirmed", "arrivals_confirmed", MetricBasis.ESTIMATED),
            ("arrival_demand_inferred", "arrivals_inferred", MetricBasis.INFERRED),
        ):
            total = sum(getattr(w, attr) for w in self.windows)
            out.append(TrafficMetric(name, total, "aircraft", basis, (evidence_id,)))
        return tuple(out)


def demand_windows(
    records: tuple[DemandRecord, ...] | None,
    fetched_at_utc: datetime | None,
    now_utc: datetime,
    horizon_minutes: int = 15,
    *,
    taxi_out_minutes: int | None = None,
    taxi_in_minutes: int | None = None,
    max_age: timedelta = DEFAULT_MAX_DEMAND_AGE,
) -> DemandWindows:
    """Bucket airport-associated flights into five-minute windows from ``now_utc``.

    Runway times are used directly. A scheduled gate time is used only when the matching
    taxi assumption is given (and is then listed in ``assumptions``); otherwise the record
    is unplaced. Cancelled flights and records without a time are unplaced. Flights
    outside the horizon are not counted. ``None`` records, or records fetched longer ago
    than ``max_age``, make demand unavailable rather than zero.
    """
    _require_utc(now_utc)
    if horizon_minutes <= 0 or horizon_minutes % WINDOW_MINUTES:
        raise ValueError("horizon_minutes must be a positive multiple of 5")
    for taxi in (taxi_out_minutes, taxi_in_minutes):
        if taxi is not None and (type(taxi) is not int or taxi < 0):
            raise ValueError("taxi minutes must be non-negative integers")
    if records is None or fetched_at_utc is None:
        return DemandWindows(None, unavailable_reason="no flight records available")
    _require_utc(fetched_at_utc)
    if now_utc - fetched_at_utc > max_age:
        return DemandWindows(None, unavailable_reason="flight records are stale")

    count = horizon_minutes // WINDOW_MINUTES
    starts = [now_utc + timedelta(minutes=WINDOW_MINUTES * i) for i in range(count)]
    counts = [[0, 0, 0, 0] for _ in range(count)]  # dep conf, dep inf, arr conf, arr inf
    assumptions: list[str] = []
    unplaced = 0
    for record in records:
        runway_time = _runway_time(record, taxi_out_minutes, taxi_in_minutes)
        if runway_time is None:
            unplaced += 1
            continue
        if record.time_kind is TimeKind.SCHEDULED_GATE:
            note = _taxi_note(record.direction, taxi_out_minutes, taxi_in_minutes)
            if note not in assumptions:
                assumptions.append(note)
        offset = (runway_time - now_utc) / timedelta(minutes=WINDOW_MINUTES)
        index = math.floor(offset)
        if not 0 <= index < count:
            continue
        slot = (0 if record.direction is FlightDirection.DEPARTURE else 2) + (
            0 if record.association is Association.CONFIRMED_RECORD else 1
        )
        counts[index][slot] += 1

    windows = tuple(
        DemandWindow(starts[i], starts[i] + timedelta(minutes=WINDOW_MINUTES), *counts[i])
        for i in range(count)
    )
    notes = ("some records could not be placed in a window",) if unplaced else ()
    quality = EvidenceQuality.LIMITED if (assumptions or unplaced) else EvidenceQuality.ADEQUATE
    return DemandWindows(windows, unplaced, tuple(assumptions), None, quality, notes)


def _runway_time(
    record: DemandRecord, taxi_out: int | None, taxi_in: int | None
) -> datetime | None:
    if record.cancelled or record.time_utc is None or record.time_kind is None:
        return None
    if record.time_kind is not TimeKind.SCHEDULED_GATE:
        return record.time_utc
    if record.direction is FlightDirection.DEPARTURE and taxi_out is not None:
        return record.time_utc + timedelta(minutes=taxi_out)
    if record.direction is FlightDirection.ARRIVAL and taxi_in is not None:
        return record.time_utc - timedelta(minutes=taxi_in)
    return None


def _taxi_note(direction: FlightDirection, taxi_out: int | None, taxi_in: int | None) -> str:
    if direction is FlightDirection.DEPARTURE:
        return f"scheduled gate-out times shifted by an assumed {taxi_out}-minute taxi-out"
    return f"scheduled gate-in times shifted by an assumed {taxi_in}-minute taxi-in"


def _require_utc(value: datetime) -> None:
    if value.utcoffset() != timedelta(0):
        raise ValueError("timestamps must be aware UTC datetimes")
