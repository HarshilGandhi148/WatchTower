import unittest
from datetime import UTC, datetime, timedelta

from agents.weather.stream import assess


class WeatherStreamTests(unittest.TestCase):
    def test_missing_stale_and_current(self):
        now = datetime.now(UTC)
        self.assertEqual(assess({}, now)["status"], "unknown")
        self.assertEqual(
            assess(
                {"weather": {"observed_at_utc": (now - timedelta(minutes=91)).isoformat()}}, now
            )["status"],
            "unknown",
        )
        current = assess({"weather": {"observed_at_utc": now.isoformat()}}, now)
        self.assertEqual(current["status"], "available")
        self.assertIsNone(current["capacity_aircraft_per_hour"])

    def test_metar_uses_its_90_minute_freshness_budget(self):
        now = datetime.now(UTC)
        for minutes in (5, 30, 60, 90):
            reading = assess(
                {"weather": {"observed_at_utc": (now - timedelta(minutes=minutes)).isoformat()}},
                now,
            )
            self.assertEqual(reading["status"], "available")
        future = assess(
            {"weather": {"observed_at_utc": (now + timedelta(seconds=1)).isoformat()}}, now
        )
        self.assertEqual(future["status"], "unknown")
