"""AeroAPI adapter: disabled by default, budget/rate guard, bounded requests, parsing."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from data.flightaware import (
    AeroApiBudget,
    AeroApiConfig,
    AeroApiConfigError,
    AeroApiParseError,
    Association,
    Endpoint,
    FlightDirection,
    TimeKind,
    build_request,
    parse_flights,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "flightaware"
NOW = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)
ENV = {
    "ENABLE_FLIGHTAWARE": "true",
    "FLIGHTAWARE_API_KEY": "test-key",
    "FLIGHTAWARE_BASE_URL": "https://aeroapi.flightaware.com/aeroapi",
    "FLIGHTAWARE_BUDGET_USD": "1.00",
    "FLIGHTAWARE_COST_PER_RESULT_SET_USD": "0.10",
}


def config(**changes: str) -> AeroApiConfig:
    result = AeroApiConfig.from_env({**ENV, **changes})
    assert result is not None
    return result


def load(name: str) -> object:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class ConfigTests(TestCase):
    def test_disabled_unless_explicitly_enabled(self) -> None:
        for value in ("false", "", "no", "TRUE-ish"):
            with self.subTest(value=value):
                self.assertIsNone(AeroApiConfig.from_env({**ENV, "ENABLE_FLIGHTAWARE": value}))
        self.assertIsNone(AeroApiConfig.from_env({}))

    def test_enabled_requires_key_budget_and_price(self) -> None:
        for name, value in (
            ("FLIGHTAWARE_API_KEY", ""),
            ("FLIGHTAWARE_API_KEY", "has space"),
            ("FLIGHTAWARE_BUDGET_USD", ""),
            ("FLIGHTAWARE_BUDGET_USD", "0"),
            ("FLIGHTAWARE_BUDGET_USD", "lots"),
            ("FLIGHTAWARE_COST_PER_RESULT_SET_USD", ""),
            ("FLIGHTAWARE_COST_PER_RESULT_SET_USD", "-0.01"),
            ("FLIGHTAWARE_BASE_URL", "http://aeroapi.flightaware.com/aeroapi"),
            ("FLIGHTAWARE_MAX_PAGES", "9"),
        ):
            with self.subTest(name=name, value=value), self.assertRaises(AeroApiConfigError):
                AeroApiConfig.from_env({**ENV, name: value})

    def test_key_is_not_in_repr(self) -> None:
        self.assertNotIn(ENV["FLIGHTAWARE_API_KEY"], repr(config()))


class BudgetTests(TestCase):
    def test_reserving_spends_before_the_call(self) -> None:
        budget = AeroApiBudget(config())
        self.assertEqual(budget.reserve(1, NOW), 1)
        self.assertEqual(budget.spent_usd, Decimal("0.10"))
        self.assertEqual(budget.remaining_usd, Decimal("0.90"))

    def test_budget_exhaustion_refuses_further_pages(self) -> None:
        budget = AeroApiBudget(config(FLIGHTAWARE_BUDGET_USD="0.25"))
        granted = [budget.reserve(1, NOW + timedelta(minutes=i)) for i in range(4)]
        self.assertEqual(granted, [1, 1, 0, 0])
        self.assertEqual(budget.spent_usd, Decimal("0.20"))

    def test_per_minute_result_set_limit(self) -> None:
        budget = AeroApiBudget(config(FLIGHTAWARE_BUDGET_USD="100"))
        granted = sum(budget.reserve(1, NOW + timedelta(seconds=i)) for i in range(15))
        self.assertEqual(granted, 10)  # Personal tier: 10 result sets per minute
        self.assertEqual(budget.reserve(1, NOW + timedelta(seconds=61)), 1)

    def test_page_cap_and_prior_spend(self) -> None:
        budget = AeroApiBudget(config(FLIGHTAWARE_MAX_PAGES="2"), Decimal("0.95"))
        self.assertEqual(budget.reserve(5, NOW), 0)  # 0.05 left < one result set
        self.assertEqual(AeroApiBudget(config(FLIGHTAWARE_MAX_PAGES="2")).reserve(5, NOW), 2)
        with self.assertRaises(ValueError):
            AeroApiBudget(config(), Decimal("-1"))


class RequestTests(TestCase):
    def test_bounded_request_with_key_in_header_only(self) -> None:
        request = build_request(
            config(), Endpoint.SCHEDULED_DEPARTURES, NOW, NOW + timedelta(hours=1), 1, "UA (TEST)"
        )
        self.assertTrue(
            request.url.startswith(
                "https://aeroapi.flightaware.com/aeroapi/airports/KDTW/flights/scheduled_departures?"
            )
        )
        self.assertIn("max_pages=1", request.url)
        self.assertIn("start=2026-10-04T16%3A00%3A00Z", request.url)
        self.assertNotIn(ENV["FLIGHTAWARE_API_KEY"], request.url)
        self.assertEqual(request.headers["x-apikey"], ENV["FLIGHTAWARE_API_KEY"])
        self.assertNotIn(ENV["FLIGHTAWARE_API_KEY"], repr(request))

    def test_window_and_pages_are_bounded(self) -> None:
        for start, end, pages in (
            (NOW, NOW, 1),
            (NOW, NOW + timedelta(hours=5), 1),  # longer than MAX_WINDOW
            (NOW, NOW + timedelta(hours=1), 0),
            (NOW, NOW + timedelta(hours=1), 2),  # more than FLIGHTAWARE_MAX_PAGES
            (datetime(2026, 10, 4, 16), NOW + timedelta(hours=1), 1),
        ):
            with self.subTest(start=start, end=end, pages=pages), self.assertRaises(ValueError):
                build_request(config(), Endpoint.SCHEDULED_ARRIVALS, start, end, pages, "UA")

    def test_cursor_is_passed_through(self) -> None:
        request = build_request(
            config(), Endpoint.SCHEDULED_ARRIVALS, NOW, NOW + timedelta(hours=1), 1, "UA", "abc"
        )
        self.assertIn("cursor=abc", request.url)


class ParseDeparturesTests(TestCase):
    def setUp(self) -> None:
        self.page = parse_flights(
            load("scheduled_departures.json"), Endpoint.SCHEDULED_DEPARTURES, NOW
        )
        self.by_id = {r.flight_id: r for r in self.page.records}

    def test_runway_time_preferred_over_gate_time(self) -> None:
        taxiing = self.by_id["TEST-DEP-TAXIING"]
        self.assertEqual(
            (taxiing.time_kind, taxiing.time_utc),
            (TimeKind.ESTIMATED_RUNWAY, datetime(2026, 10, 4, 16, 6, tzinfo=UTC)),
        )
        self.assertEqual(self.by_id["TEST-DEP-FUTURE"].time_kind, TimeKind.SCHEDULED_RUNWAY)
        self.assertEqual(self.by_id["TEST-DEP-GATE-ONLY"].time_kind, TimeKind.SCHEDULED_GATE)

    def test_gate_times_and_destination_are_kept(self) -> None:
        taxiing = self.by_id["TEST-DEP-TAXIING"]
        self.assertEqual(taxiing.gate_actual_utc, datetime(2026, 10, 4, 15, 55, tzinfo=UTC))
        self.assertEqual(taxiing.gate_scheduled_utc, datetime(2026, 10, 4, 15, 50, tzinfo=UTC))
        self.assertEqual(taxiing.other_airport_icao, "KORD")
        self.assertIsNone(self.by_id["TEST-DEP-GATE-ONLY"].other_airport_icao)
        self.assertEqual(taxiing.direction, FlightDirection.DEPARTURE)
        self.assertEqual(taxiing.association, Association.CONFIRMED_RECORD)

    def test_cancelled_is_kept_but_excluded_from_demand(self) -> None:
        cancelled = self.by_id["TEST-DEP-CANCELLED"]
        self.assertTrue(cancelled.cancelled and cancelled.excluded_from_demand)

    def test_bad_records_are_rejected_and_counted(self) -> None:
        self.assertNotIn("TEST-DEP-WRONG-ORIGIN", self.by_id)
        self.assertEqual(self.page.rejected_records, 3)  # wrong origin, no id, non-object
        self.assertEqual(self.page.duplicates_dropped, 1)
        self.assertEqual(len(self.page.records), 6)

    def test_invalid_and_naive_times_stay_unknown_and_flagged(self) -> None:
        bad = self.by_id["TEST-DEP-BAD-TIME"]
        self.assertIsNone(bad.gate_scheduled_utc)
        self.assertIsNone(bad.time_utc)
        self.assertIn("invalid_scheduled_out", bad.notes)
        self.assertIn("naive_estimated_off", bad.notes)

    def test_complete_page_has_no_more(self) -> None:
        self.assertFalse(self.page.has_more)
        self.assertIsNone(self.page.next_cursor)


class ParseArrivalsAndFailureTests(TestCase):
    def test_arrivals_use_runway_times_and_flag_diversions(self) -> None:
        page = parse_flights(load("scheduled_arrivals.json"), Endpoint.SCHEDULED_ARRIVALS, NOW)
        by_id = {r.flight_id: r for r in page.records}
        arrival = by_id["TEST-ARR-ESTIMATED"]
        self.assertEqual(
            (arrival.direction, arrival.time_kind),
            (FlightDirection.ARRIVAL, TimeKind.ESTIMATED_RUNWAY),
        )
        self.assertEqual(arrival.other_airport_icao, "KORD")
        self.assertTrue(by_id["TEST-ARR-DIVERTED"].excluded_from_demand)

    def test_truncated_page_is_flagged_and_exposes_the_cursor(self) -> None:
        page = parse_flights(load("scheduled_arrivals.json"), Endpoint.SCHEDULED_ARRIVALS, NOW)
        self.assertTrue(page.has_more)
        self.assertEqual(page.next_cursor, "TESTCURSOR123")

    def test_any_next_link_means_more_even_without_a_cursor(self) -> None:
        payload = {"scheduled_arrivals": [], "links": {"next": "/airports/KDTW/next-page"}}
        page = parse_flights(payload, Endpoint.SCHEDULED_ARRIVALS, NOW)
        self.assertTrue(page.has_more)
        self.assertIsNone(page.next_cursor)

    def test_empty_list_is_valid_but_missing_list_is_an_error(self) -> None:
        empty = parse_flights({"scheduled_departures": []}, Endpoint.SCHEDULED_DEPARTURES, NOW)
        self.assertEqual(empty.records, ())
        for payload in (None, [], {}, {"scheduled_departures": None}, {"scheduled_arrivals": []}):
            with self.subTest(payload=payload), self.assertRaises(AeroApiParseError):
                parse_flights(payload, Endpoint.SCHEDULED_DEPARTURES, NOW)

    def test_naive_fetch_time_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            parse_flights(
                {"scheduled_departures": []}, Endpoint.SCHEDULED_DEPARTURES, datetime(2026, 10, 4)
            )
