"""Nearby traffic and airport-associated demand stay separate; unknown is never zero."""

from datetime import UTC, datetime, timedelta
from unittest import TestCase

from agents.congestion.metrics import (
    KDTW_REFERENCE_POINT,
    NearbyTraffic,
    demand_windows,
    distance_nm,
    nearby_traffic,
    traffic_change,
)
from data.adsb import AdsbObservation, AdsbSnapshot
from data.flightaware import Association, DemandRecord, FlightDirection, TimeKind
from shared.contracts import EvidenceQuality, MetricBasis

NOW = datetime(2026, 10, 4, 1, 0, 0, tzinfo=UTC)
LAT, LON = KDTW_REFERENCE_POINT


def aircraft(
    ident: str,
    *,
    dlat: float = 0.1,
    age: float | None = 2.0,
    on_ground: bool | None = False,
    position: bool = True,
) -> AdsbObservation:
    return AdsbObservation(
        aircraft_id=ident,
        callsign=None,
        latitude_deg=LAT + dlat if position else None,
        longitude_deg=LON if position else None,
        altitude_ft=None if on_ground else 3000.0,
        on_ground=on_ground,
        ground_speed_kt=None,
        track_deg=None,
        position_age_s=age,
        observed_at_utc=None,
        provider_time_utc=None,
        fetched_at_utc=NOW,
        missing_fields=(),
        quality_flags=(),
    )


def snapshot(*observations: AdsbObservation, fetched: datetime = NOW) -> AdsbSnapshot:
    return AdsbSnapshot("adsb.lol", None, fetched, tuple(observations), None, 0, 0)


class DistanceTests(TestCase):
    def test_one_degree_of_latitude_is_about_sixty_nautical_miles(self) -> None:
        self.assertAlmostEqual(distance_nm(LAT, LON, LAT + 1, LON), 60.0, delta=0.2)

    def test_zero_distance_and_symmetry(self) -> None:
        self.assertEqual(distance_nm(LAT, LON, LAT, LON), 0.0)
        self.assertAlmostEqual(distance_nm(0, 0, 10, 20), distance_nm(10, 20, 0, 0))


class NearbyTrafficTests(TestCase):
    def test_counts_airborne_and_ground_separately(self) -> None:
        result = nearby_traffic(
            snapshot(
                aircraft("a1"),
                aircraft("a2"),
                aircraft("g1", on_ground=True),
                aircraft("u1", on_ground=None),
            ),
            NOW,
        )
        self.assertEqual((result.total, result.airborne, result.on_ground), (4, 2, 1))
        self.assertEqual(result.unknown_ground_state, 1)
        self.assertEqual(result.quality, EvidenceQuality.ADEQUATE)

    def test_aircraft_beyond_radius_are_excluded_not_counted(self) -> None:
        result = nearby_traffic(snapshot(aircraft("near"), aircraft("far", dlat=1.0)), NOW)
        self.assertEqual((result.total, result.outside_radius), (1, 1))

    def test_stale_and_unpositioned_aircraft_are_excluded_as_unknown(self) -> None:
        result = nearby_traffic(
            snapshot(
                aircraft("ok"),
                aircraft("old", age=120.0),
                aircraft("noage", age=None),
                aircraft("nopos", position=False),
            ),
            NOW,
        )
        self.assertEqual(result.total, 1)
        self.assertEqual((result.excluded_stale, result.excluded_no_position), (2, 1))
        self.assertEqual(result.quality, EvidenceQuality.LIMITED)

    def test_missing_snapshot_is_unavailable_not_zero(self) -> None:
        result = nearby_traffic(None, NOW)
        self.assertIsNone(result.total)
        self.assertEqual(result.quality, EvidenceQuality.UNAVAILABLE)
        self.assertEqual(result.metrics("ev-1"), ())

    def test_stale_snapshot_is_unavailable(self) -> None:
        old = snapshot(aircraft("a1"), fetched=NOW - timedelta(minutes=5))
        self.assertIsNone(nearby_traffic(old, NOW).total)

    def test_no_aircraft_with_a_fresh_position_is_unavailable(self) -> None:
        result = nearby_traffic(snapshot(aircraft("old", age=500.0)), NOW)
        self.assertIsNone(result.total)

    def test_empty_response_is_zero_but_only_limited_quality(self) -> None:
        result = nearby_traffic(snapshot(), NOW)
        self.assertEqual(result.total, 0)
        self.assertEqual(result.quality, EvidenceQuality.LIMITED)
        self.assertTrue(any("coverage" in note for note in result.notes))

    def test_metrics_are_observed_with_units_and_evidence(self) -> None:
        metrics = nearby_traffic(
            snapshot(aircraft("a1"), aircraft("g1", on_ground=True)), NOW
        ).metrics("adsb-7")
        self.assertEqual(
            {m.name: m.value for m in metrics},
            {
                "nearby_aircraft_total": 2,
                "nearby_aircraft_airborne": 1,
                "nearby_aircraft_on_ground": 1,
            },
        )
        for metric in metrics:
            self.assertEqual((metric.unit, metric.basis), ("aircraft", MetricBasis.OBSERVED))
            self.assertEqual(metric.evidence_ids, ("adsb-7",))

    def test_nearby_metrics_never_claim_airport_demand(self) -> None:
        names = {m.name for m in nearby_traffic(snapshot(aircraft("a1")), NOW).metrics("e")}
        self.assertFalse(any("demand" in name or "arrival" in name for name in names))

    def test_rejects_naive_time_and_bad_radius(self) -> None:
        with self.assertRaises(ValueError):
            nearby_traffic(snapshot(), datetime(2026, 10, 4, 1, 0))
        with self.assertRaises(ValueError):
            nearby_traffic(snapshot(), NOW, radius_nm=0)


class TrafficChangeTests(TestCase):
    def count(self, total: int | None, radius: float = 40.0) -> NearbyTraffic:
        return NearbyTraffic(total, total, 0, 0, 0, 0, 0, radius, EvidenceQuality.ADEQUATE)

    def test_change_is_difference_of_known_comparable_counts(self) -> None:
        self.assertEqual(traffic_change(self.count(9), self.count(5)), 4)
        self.assertEqual(traffic_change(self.count(3), self.count(5)), -2)

    def test_unknown_or_incomparable_change_is_none_not_zero(self) -> None:
        self.assertIsNone(traffic_change(self.count(9), None))
        self.assertIsNone(traffic_change(self.count(None), self.count(5)))
        self.assertIsNone(traffic_change(self.count(9), self.count(None)))
        self.assertIsNone(traffic_change(self.count(9, 40.0), self.count(5, 25.0)))


def record(
    ident: str,
    minutes: float | None,
    *,
    direction: FlightDirection = FlightDirection.DEPARTURE,
    kind: TimeKind | None = TimeKind.ESTIMATED_RUNWAY,
    association: Association = Association.CONFIRMED_RECORD,
    cancelled: bool = False,
) -> DemandRecord:
    when = None if minutes is None else NOW + timedelta(minutes=minutes)
    return DemandRecord(
        ident, direction, when, None if when is None else kind, association, cancelled
    )


class DemandWindowTests(TestCase):
    fetched = NOW - timedelta(minutes=1)

    def run_windows(self, records: tuple[DemandRecord, ...] | None, **kwargs: object):  # type: ignore[no-untyped-def]
        return demand_windows(records, self.fetched, NOW, **kwargs)  # type: ignore[arg-type]

    def test_flights_land_in_five_minute_windows(self) -> None:
        result = self.run_windows(
            (
                record("d1", 0),
                record("d2", 4.9),
                record("d3", 5),
                record("a1", 12, direction=FlightDirection.ARRIVAL),
            )
        )
        assert result.windows
        self.assertEqual(len(result.windows), 3)
        self.assertEqual([w.departures_confirmed for w in result.windows], [2, 1, 0])
        self.assertEqual([w.arrivals_confirmed for w in result.windows], [0, 0, 1])
        self.assertEqual(result.windows[1].start_utc, NOW + timedelta(minutes=5))
        self.assertEqual(result.quality, EvidenceQuality.ADEQUATE)

    def test_inferred_association_is_not_mixed_with_confirmed(self) -> None:
        result = self.run_windows(
            (record("d1", 1), record("d2", 1, association=Association.INFERRED))
        )
        assert result.windows
        window = result.windows[0]
        self.assertEqual((window.departures_confirmed, window.departures_inferred), (1, 1))
        by_name = {m.name: m for m in result.metrics("flights-1")}
        self.assertEqual(by_name["departure_demand_confirmed"].value, 1)
        self.assertEqual(by_name["departure_demand_inferred"].basis, MetricBasis.INFERRED)

    def test_missing_records_or_fetch_time_is_unavailable_not_zero(self) -> None:
        for records, fetched in ((None, self.fetched), ((), None)):
            with self.subTest(records=records, fetched=fetched):
                result = demand_windows(records, fetched, NOW)
                self.assertIsNone(result.windows)
                self.assertEqual(result.quality, EvidenceQuality.UNAVAILABLE)
                self.assertEqual(result.metrics("e"), ())

    def test_stale_records_are_unavailable(self) -> None:
        result = demand_windows((record("d1", 1),), NOW - timedelta(minutes=10), NOW)
        self.assertIsNone(result.windows)
        self.assertEqual(result.unavailable_reason, "flight records are stale")

    def test_empty_but_fresh_records_give_zero_demand_windows(self) -> None:
        result = self.run_windows(())
        assert result.windows
        self.assertEqual(sum(w.departures_confirmed for w in result.windows), 0)

    def test_cancelled_and_untimed_records_are_unplaced(self) -> None:
        result = self.run_windows(
            (record("c1", 3, cancelled=True), record("n1", None), record("d1", 3))
        )
        assert result.windows
        self.assertEqual(result.unplaced_records, 2)
        self.assertEqual(result.windows[0].departures_confirmed, 1)
        self.assertEqual(result.quality, EvidenceQuality.LIMITED)

    def test_records_outside_the_horizon_are_not_counted(self) -> None:
        result = self.run_windows((record("past", -1), record("late", 15), record("late2", 90)))
        assert result.windows
        self.assertEqual(sum(w.departures_confirmed for w in result.windows), 0)

    def test_gate_times_need_an_explicit_taxi_assumption(self) -> None:
        gate = record("d1", 2, kind=TimeKind.SCHEDULED_GATE)
        without = self.run_windows((gate,))
        assert without.windows
        self.assertEqual(
            (without.unplaced_records, sum(w.departures_confirmed for w in without.windows)), (1, 0)
        )
        shifted = self.run_windows((gate,), taxi_out_minutes=10)
        assert shifted.windows
        self.assertEqual([w.departures_confirmed for w in shifted.windows], [0, 0, 1])
        self.assertEqual(len(shifted.assumptions), 1)
        self.assertIn("10-minute taxi-out", shifted.assumptions[0])
        self.assertEqual(shifted.quality, EvidenceQuality.LIMITED)

    def test_arrival_gate_times_use_taxi_in_in_the_other_direction(self) -> None:
        arrival = record("a1", 16, direction=FlightDirection.ARRIVAL, kind=TimeKind.SCHEDULED_GATE)
        result = self.run_windows((arrival,), taxi_in_minutes=8)
        assert result.windows
        self.assertEqual([w.arrivals_confirmed for w in result.windows], [0, 1, 0])
        self.assertEqual(self.run_windows((arrival,), taxi_out_minutes=8).unplaced_records, 1)

    def test_runway_times_need_no_assumption(self) -> None:
        result = self.run_windows((record("d1", 2, kind=TimeKind.ACTUAL_RUNWAY),))
        self.assertEqual(result.assumptions, ())

    def test_horizon_and_taxi_validation(self) -> None:
        for horizon in (0, 7, -5):
            with self.subTest(horizon=horizon), self.assertRaises(ValueError):
                self.run_windows((), horizon_minutes=horizon)
        with self.assertRaises(ValueError):
            self.run_windows((), taxi_out_minutes=-1)
        self.assertEqual(len(self.run_windows((), horizon_minutes=30).windows or ()), 6)

    def test_demand_record_rejects_inconsistent_time_fields(self) -> None:
        with self.assertRaises(ValueError):
            DemandRecord("f", FlightDirection.DEPARTURE, NOW, None, Association.INFERRED)
        with self.assertRaises(ValueError):
            DemandRecord(
                "f", FlightDirection.DEPARTURE, datetime(2026, 10, 4, 1), TimeKind.ACTUAL_RUNWAY,
                Association.INFERRED,
            )  # fmt: skip
