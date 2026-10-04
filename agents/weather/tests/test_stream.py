import unittest
from datetime import UTC, datetime, timedelta

from agents.weather.stream import assess


class WeatherStreamTests(unittest.TestCase):
    def test_missing_stale_and_current(self):
        now = datetime.now(UTC)
        self.assertEqual(assess({}, now)["status"], "unknown")
        self.assertEqual(
            assess({"weather": {"observed_at_utc": (now - timedelta(hours=1)).isoformat()}}, now)[
                "status"
            ],
            "unknown",
        )
        current = assess({"weather": {"observed_at_utc": now.isoformat()}}, now)
        self.assertEqual(current["status"], "available")
        self.assertIsNone(current["capacity_aircraft_per_hour"])
