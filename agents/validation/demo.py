"""Demo watch recommendations and constant-track forecasts from copied observations."""

import hashlib
import math
from datetime import timedelta

from data.evidence_inputs import FeedSnapshot
from data.traffic_metrics import distance_nm
from shared.validation_report import AircraftRecommendation


def aircraft_recommendations(feeds: FeedSnapshot) -> tuple[AircraftRecommendation, ...]:
    source, cutoff = feeds.adsb, feeds.cutoff_at_utc
    if source is None or cutoff is None or not feeds.traffic_evidence_id:
        return ()
    if not timedelta(0) <= cutoff - source.fetched_at_utc <= timedelta(seconds=60):
        return ()
    center = (feeds.configuration.latitude_deg, feeds.configuration.longitude_deg)
    rows = [
        r
        for r in source.observations
        if r.has_position
        and not r.is_stale()
        and r.observed_at_utc is not None
        and timedelta(0) <= cutoff - r.observed_at_utc <= timedelta(seconds=30)
        and distance_nm(*center, r.latitude_deg, r.longitude_deg) <= feeds.configuration.radius_nm
    ]
    rows.sort(
        key=lambda r: (
            r.on_ground is not False,
            distance_nm(*center, r.latitude_deg, r.longitude_deg),
        )
    )
    # Rotate the closest fresh aircraft so successive demo recommendations vary.
    pool = rows[:12]
    if not pool:
        return ()
    offset = int.from_bytes(hashlib.sha256(feeds.case_id.encode()).digest()[:2]) % len(pool)
    selected = (pool[offset:] + pool[:offset])[:3]
    result = []
    for row in selected:
        assert (
            row.latitude_deg is not None
            and row.longitude_deg is not None
            and row.observed_at_utc is not None
        )
        label = row.callsign or row.aircraft_id.upper()
        lat = lon = None
        at = None
        action = f"Keep {label} on the traffic watchlist and review its next position update."
        prediction = "Watch the next aircraft update for movement."
        if row.ground_speed_kt is not None and row.track_deg is not None and row.on_ground is False:
            minutes = 2
            travel = row.ground_speed_kt * minutes / 60
            distance = travel / 3440.065
            bearing = math.radians(row.track_deg)
            p, l = math.radians(row.latitude_deg), math.radians(row.longitude_deg)
            p2 = math.asin(
                math.sin(p) * math.cos(distance)
                + math.cos(p) * math.sin(distance) * math.cos(bearing)
            )
            l2 = l + math.atan2(
                math.sin(bearing) * math.sin(distance) * math.cos(p),
                math.cos(distance) - math.sin(p) * math.sin(p2),
            )
            lat, lon = math.degrees(p2), (math.degrees(l2) + 180) % 360 - 180
            at = row.observed_at_utc + timedelta(minutes=minutes)
            before, after = (
                distance_nm(*center, row.latitude_deg, row.longitude_deg),
                distance_nm(*center, lat, lon),
            )
            direction = (
                "toward"
                if after < before - 0.25
                else "away from"
                if after > before + 0.25
                else "through"
            )
            action = f"Monitor {label}'s track {direction} the KDTW area over the next 2 minutes."
            prediction = f"If its current speed and heading continue, {label} may travel {travel:.1f} NM in 2 minutes and reach about {after:.1f} NM from KDTW."
        elif row.on_ground is True:
            action = f"Watch {label}'s ground movement and review the next position update."
            prediction = "Currently observed on the ground; the next update can show movement."
        result.append(
            AircraftRecommendation(
                row.aircraft_id,
                label,
                action,
                prediction,
                feeds.traffic_evidence_id,
                row.observed_at_utc,
                at,
                lat,
                lon,
            )
        )
    return tuple(result)
