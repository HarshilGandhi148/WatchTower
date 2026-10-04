import unittest
from datetime import UTC, datetime

from agents.congestion.stream import assess


class CongestionStreamTests(unittest.TestCase):
    def test_empty_success_differs_from_failed_missing_and_demand_unknown(self):
        now = datetime.now(UTC)
        for payload in ({}, {"traffic": {"fetch_succeeded": False}}):
            self.assertIsNone(assess(payload, now)["nearby_aircraft"])
        payload = {
            "traffic": {
                "fetch_succeeded": True,
                "fetched_at_utc": now.isoformat(),
                "observations": [],
            }
        }
        result = assess(payload, now)
        self.assertEqual(result["nearby_aircraft"], 0)
        self.assertIsNone(result["departure_demand"])
        self.assertIsNone(result["projection"])

    def test_stale_or_missing_positions_are_unknown(self):
        now = datetime.now(UTC)
        for row in ({}, {"observed_at_utc": now.isoformat()}):
            result = assess(
                {
                    "traffic": {
                        "fetch_succeeded": True,
                        "fetched_at_utc": now.isoformat(),
                        "observations": [row],
                    }
                },
                now,
            )
            self.assertIsNone(result["nearby_aircraft"])
