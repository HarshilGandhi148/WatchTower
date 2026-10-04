"""Dormant AeroAPI fetch helper: caching, budget refusal, truncation and failures."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest import TestCase

from data.flightaware import AeroApiConfig, Endpoint, parse_flights
from data.flightaware_fetch import AeroApiFetcher

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "flightaware"
NOW = datetime(2026, 10, 4, 16, 0, tzinfo=UTC)
AERO_ENV = {
    "ENABLE_FLIGHTAWARE": "true",
    "FLIGHTAWARE_API_KEY": "test-key",
    "FLIGHTAWARE_BASE_URL": "https://aeroapi.flightaware.com/aeroapi",
    "FLIGHTAWARE_BUDGET_USD": "1.00",
    "FLIGHTAWARE_COST_PER_RESULT_SET_USD": "0.10",
}


def departures_json() -> dict:  # type: ignore[type-arg]
    return json.loads((FIXTURES / "scheduled_departures.json").read_text(encoding="utf-8"))


def arrivals_json(truncated: bool = False) -> dict:  # type: ignore[type-arg]
    data = json.loads((FIXTURES / "scheduled_arrivals.json").read_text(encoding="utf-8"))
    if not truncated:
        data["links"] = None
    return data


def records() -> tuple:  # type: ignore[type-arg]
    dep = parse_flights(departures_json(), Endpoint.SCHEDULED_DEPARTURES, NOW).records
    arr = parse_flights(arrivals_json(), Endpoint.SCHEDULED_ARRIVALS, NOW).records
    return dep + arr


def aero_config(**changes: str) -> AeroApiConfig:
    config = AeroApiConfig.from_env({**AERO_ENV, **changes})
    assert config is not None
    return config


class FetcherTests(TestCase):
    def fake_get(self, *, fail: bool = False, truncated: bool = False):  # type: ignore[no-untyped-def]
        calls: list[str] = []

        def get(url: str, headers: dict) -> bytes:  # type: ignore[type-arg]
            calls.append(url)
            self.assertEqual(headers["x-apikey"], AERO_ENV["FLIGHTAWARE_API_KEY"])
            if fail:
                raise OSError("connection reset")
            if "scheduled_departures" in url:
                return json.dumps(departures_json()).encode()
            return json.dumps(arrivals_json(truncated)).encode()

        get.calls = calls  # type: ignore[attr-defined]
        return get

    def test_fetch_combines_both_endpoints_and_spends_from_the_budget(self) -> None:
        get = self.fake_get()
        fetcher = AeroApiFetcher(aero_config(), "UA (TEST)", get=get)
        recs, fetched_at, error = fetcher.fetch(NOW)
        assert recs is not None
        self.assertIsNone(error)
        self.assertEqual(fetched_at, NOW)
        self.assertEqual(len(get.calls), 2)
        self.assertEqual(fetcher.budget.spent_usd, Decimal("0.20"))
        departures_url, arrivals_url = get.calls
        self.assertIn("start=2026-10-04T14%3A00%3A00Z", departures_url)  # 2 h lookback
        self.assertIn("start=2026-10-04T16%3A00%3A00Z", arrivals_url)  # arrivals from now
        self.assertIn("end=2026-10-04T17%3A00%3A00Z", arrivals_url)
        self.assertTrue(any(r.flight_id == "TEST-ARR-ESTIMATED" for r in recs))

    def test_cache_prevents_paid_calls_inside_the_refresh_period(self) -> None:
        get = self.fake_get()
        fetcher = AeroApiFetcher(aero_config(), "UA (TEST)", get=get)
        fetcher.fetch(NOW)
        _, fetched_at, _ = fetcher.fetch(NOW + timedelta(minutes=4))
        self.assertEqual((len(get.calls), fetched_at), (2, NOW))
        fetcher.fetch(NOW + timedelta(minutes=5))
        self.assertEqual(len(get.calls), 4)

    def test_budget_exhaustion_makes_demand_unavailable(self) -> None:
        get = self.fake_get()
        fetcher = AeroApiFetcher(aero_config(FLIGHTAWARE_BUDGET_USD="0.15"), "UA", get=get)
        recs, _, error = fetcher.fetch(NOW)
        self.assertIsNone(recs)
        self.assertIn("budget", error or "")
        self.assertEqual(len(get.calls), 1)  # the refused request was never sent

    def test_failures_and_truncation_are_unavailable_and_never_leak_the_key(self) -> None:
        for get, expected in (
            (self.fake_get(fail=True), "OSError"),
            (self.fake_get(truncated=True), "MAX_PAGES"),
        ):
            with self.subTest(expected=expected):
                recs, _, error = AeroApiFetcher(aero_config(), "UA", get=get).fetch(NOW)
                self.assertIsNone(recs)
                self.assertIn(expected, error or "")
                self.assertNotIn(AERO_ENV["FLIGHTAWARE_API_KEY"], error or "")
