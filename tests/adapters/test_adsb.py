"""ADSB.lol adapter: success, empty, malformed, missing fields, duplicates, untrusted text."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import TestCase

from data.adsb import (
    AdsbObservation,
    AdsbParseError,
    AdsbSnapshot,
    parse_point_response,
    point_url,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "adsb" / "point_response.json"
FETCHED = datetime(2026, 9, 21, 4, 53, 21, tzinfo=UTC)


def load() -> dict:  # type: ignore[type-arg]
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


class PointUrlTests(TestCase):
    def test_builds_documented_query(self) -> None:
        self.assertEqual(
            point_url("https://api.adsb.lol/", 42.2124, -83.3534, 40),
            "https://api.adsb.lol/v2/point/42.2124/-83.3534/40",
        )

    def test_rejects_bad_center_radius_and_scheme(self) -> None:
        for args in (
            ("https://x", 91, 0, 40),
            ("https://x", 0, 181, 40),
            ("https://x", float("nan"), 0, 40),
            ("https://x", 0, 0, 0),
            ("https://x", 0, 0, 251),
            ("https://x", 0, 0, 40.5),
            ("http://x", 0, 0, 40),
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                point_url(*args)  # type: ignore[arg-type]


class ParseTests(TestCase):
    def setUp(self) -> None:
        self.snapshot = parse_point_response(load(), FETCHED)
        self.by_id = {o.aircraft_id: o for o in self.snapshot.observations}

    def test_provider_time_and_fetch_time_stay_separate(self) -> None:
        self.assertEqual(self.snapshot.provider_time_utc, datetime.fromtimestamp(1790000000, UTC))
        self.assertEqual(self.snapshot.fetched_at_utc, FETCHED)
        a = self.by_id["a1b2c3"]
        assert a.observed_at_utc and a.provider_time_utc
        self.assertEqual(a.provider_time_utc - a.observed_at_utc, timedelta(seconds=1.2))

    def test_normalizes_values_and_units(self) -> None:
        a = self.by_id["a1b2c3"]
        self.assertEqual(
            (a.callsign, a.altitude_ft, a.ground_speed_kt, a.track_deg, a.position_age_s),
            ("DAL123", 3500.0, 180.5, 270.0, 1.2),
        )
        self.assertIs(a.on_ground, False)
        self.assertTrue(a.has_position)
        self.assertEqual(a.missing_fields, ())

    def test_duplicate_hex_keeps_freshest_position_and_is_counted(self) -> None:
        self.assertEqual(self.snapshot.duplicates_dropped, 1)
        self.assertEqual(self.by_id["a1b2c3"].position_age_s, 1.2)
        ids = [o.aircraft_id for o in self.snapshot.observations]
        self.assertEqual(len(ids), len(set(ids)))

    def test_on_ground_is_not_a_missing_altitude(self) -> None:
        g = self.by_id["abcdef"]
        self.assertIs(g.on_ground, True)
        self.assertIsNone(g.altitude_ft)
        self.assertNotIn("altitude", g.missing_fields)

    def test_missing_fields_stay_unknown_not_zero(self) -> None:
        o = self.by_id["0a0b0c"]
        self.assertIsNone(o.callsign)
        self.assertIsNone(o.ground_speed_kt)
        self.assertIsNone(o.track_deg)
        self.assertEqual(o.missing_fields, ("callsign", "ground_speed", "track"))

    def test_stale_and_unknown_age_are_both_stale(self) -> None:
        self.assertTrue(self.by_id["0a0b0c"].is_stale())
        self.assertFalse(self.by_id["a1b2c3"].is_stale())
        self.assertTrue(self.by_id["112233"].is_stale())  # no seen_pos -> unknown age

    def test_prompt_injection_callsign_is_dropped_not_interpreted(self) -> None:
        o = self.by_id["112233"]
        self.assertIsNone(o.callsign)
        self.assertIn("callsign_rejected", o.quality_flags)
        self.assertFalse(o.has_position)

    def test_half_a_position_is_discarded(self) -> None:
        o = self.by_id["445566"]
        self.assertIsNone(o.latitude_deg)
        self.assertIsNone(o.longitude_deg)
        self.assertIn("incomplete_position", o.quality_flags)

    def test_implausible_value_becomes_unknown_and_flagged(self) -> None:
        o = self.by_id["~778899"]
        self.assertIsNone(o.ground_speed_kt)
        self.assertIn("invalid_gs", o.quality_flags)
        self.assertIn("non_icao_address", o.quality_flags)

    def test_invalid_records_are_rejected_and_counted(self) -> None:
        self.assertEqual(self.snapshot.rejected_records, 2)  # bad hex, non-object
        self.assertEqual(self.snapshot.reported_total, 9)
        self.assertEqual(len(self.snapshot.observations), 6)

    def test_nearby_aircraft_carry_no_airport_association(self) -> None:
        fields = set(AdsbObservation.__dataclass_fields__)
        self.assertFalse({"destination", "origin", "airport", "is_arrival"} & fields)


class EmptyAndMalformedTests(TestCase):
    def test_empty_response_is_a_valid_empty_snapshot(self) -> None:
        snap = parse_point_response({"ac": [], "now": 1790000000000, "total": 0}, FETCHED)
        self.assertIsInstance(snap, AdsbSnapshot)
        self.assertEqual(snap.observations, ())

    def test_malformed_payloads_raise_instead_of_looking_like_zero_traffic(self) -> None:
        for payload in (None, [], "oops", {}, {"ac": None}, {"ac": "x"}, {"msg": "error"}):
            with self.subTest(payload=payload), self.assertRaises(AdsbParseError):
                parse_point_response(payload, FETCHED)

    def test_missing_or_bad_provider_time_leaves_observation_time_unknown(self) -> None:
        for now in (None, "x", -5, float("inf")):
            payload = {"now": now, "ac": [{"hex": "a1b2c3", "lat": 1, "lon": 2, "seen_pos": 1}]}
            with self.subTest(now=now):
                snap = parse_point_response(payload, FETCHED)
                self.assertIsNone(snap.provider_time_utc)
                self.assertIsNone(snap.observations[0].observed_at_utc)
                self.assertEqual(snap.observations[0].position_age_s, 1.0)

    def test_epoch_seconds_are_accepted_as_well_as_milliseconds(self) -> None:
        snap = parse_point_response({"now": 1790000000, "ac": []}, FETCHED)
        self.assertEqual(snap.provider_time_utc, datetime.fromtimestamp(1790000000, UTC))

    def test_naive_fetch_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_point_response({"ac": []}, datetime(2026, 9, 21, 4, 53))

    def test_boolean_and_nan_numbers_are_not_values(self) -> None:
        record = {"hex": "a1b2c3", "lat": True, "lon": float("nan"), "gs": False}
        obs = parse_point_response({"ac": [record]}, FETCHED).observations[0]
        self.assertFalse(obs.has_position)
        self.assertIsNone(obs.ground_speed_kt)
