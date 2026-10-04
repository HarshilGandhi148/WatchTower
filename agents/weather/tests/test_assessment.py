"""Evidence-backed weather findings; no model or provider access."""

from dataclasses import replace
from datetime import timedelta
from unittest import TestCase

from agents.weather.assessment import WeatherRules, assess_weather
from agents.weather.tests.helpers import NOW, metar_payload, request, snapshot, taf_payload
from data.awc import parse_metar_response
from shared.contracts import EvidenceQuality, WeatherBasis, WeatherStatus


class AssessmentTests(TestCase):
    def test_clear_weather_with_full_coverage(self) -> None:
        snap = snapshot()
        result = assess_weather(request(snap), snap, NOW)
        self.assertEqual(result.status, WeatherStatus.NO_MATERIAL_IMPACT)
        self.assertEqual(result.quality, EvidenceQuality.ADEQUATE)
        self.assertEqual(result.hazards, ())
        self.assertEqual(result.missing_inputs, ())
        self.assertEqual(result.envelope.reply_to, "request-1")
        self.assertTrue(all(f.evidence_id in snap.evidence_ids for f in result.weather_facts))

    def test_processing_delay_does_not_create_missing_history(self) -> None:
        snap = snapshot()
        result = assess_weather(request(snap), snap, NOW + timedelta(seconds=2))
        self.assertEqual(result.status, WeatherStatus.NO_MATERIAL_IMPACT)
        self.assertEqual(result.missing_inputs, ())

    def test_wind_changes_require_both_retained_reports(self) -> None:
        snap = snapshot(metar={**metar_payload(), "wspd": 18, "wdir": 60})
        previous = parse_metar_response(
            [{**metar_payload(), "obsTime": NOW.timestamp() - 3600, "wspd": 8, "wdir": 350}], NOW
        )[0]
        snap = replace(
            snap,
            metars=(previous, *snap.metars),
            evidence_ids=(*snap.evidence_ids, previous.evidence_id),
        )
        result = assess_weather(request(snap), snap, NOW)
        self.assertEqual({h.kind for h in result.hazards}, {"wind_change", "direction_change"})
        changes = [f for f in result.weather_facts if f.related_evidence_ids]
        self.assertEqual({f.value for f in changes}, {10, 70})
        self.assertTrue(all(f.related_evidence_ids == (previous.evidence_id,) for f in changes))
        self.assertIn(previous.evidence_id, result.summary)

    def test_malformed_conditional_field_cannot_inherit_clear_evidence(self) -> None:
        taf = taf_payload()
        taf["fcsts"].append(
            {
                "timeFrom": NOW.timestamp(),
                "timeTo": (NOW + timedelta(minutes=15)).timestamp(),
                "fcstChange": "TEMPO",
                "visib": "malformed",
            }
        )
        snap = snapshot(taf=taf)
        result = assess_weather(request(snap), snap, NOW)
        self.assertIn("conditional:invalid_visibility", result.missing_inputs)
        self.assertNotEqual(result.status, WeatherStatus.NO_MATERIAL_IMPACT)

    def test_boundary_rules_and_explicit_units(self) -> None:
        metar = {
            **metar_payload(),
            "wspd": 20,
            "wgst": 30,
            "visib": 3,
            "clouds": [{"cover": "BKN", "base": 1000}],
        }
        snap = snapshot(metar=metar)
        result = assess_weather(request(snap), snap, NOW)
        self.assertEqual({h.kind for h in result.hazards}, {"wind", "gusts"})
        self.assertEqual(result.status, WeatherStatus.IMPACT)
        metar.update(visib=2.99, clouds=[{"cover": "BKN", "base": 999}])
        snap = snapshot(metar=metar)
        result = assess_weather(request(snap), snap, NOW)
        self.assertEqual(
            {h.kind for h in result.hazards}, {"wind", "gusts", "visibility", "ceiling"}
        )
        self.assertIn("wind_speed_kt: 20 kt", result.summary)
        self.assertTrue(any(f.unit == "m" for f in result.weather_facts))

    def test_missing_stale_and_partial_are_not_clear(self) -> None:
        packets = [
            snapshot(taf=False),
            snapshot(metar=False),
            snapshot(metar=False, taf=False),
            snapshot(metar={**metar_payload(), "obsTime": NOW.timestamp() - 5401}),
        ]
        for snap in packets:
            result = assess_weather(request(snap), snap, NOW)
            self.assertEqual(result.status, WeatherStatus.INSUFFICIENT_DATA)
            self.assertTrue(result.missing_inputs)
        self.assertEqual(
            assess_weather(request(packets[2]), packets[2], NOW).quality,
            EvidenceQuality.UNAVAILABLE,
        )
        adverse = snapshot(metar={**metar_payload(), "wspd": 25}, taf=False)
        result = assess_weather(request(adverse), adverse, NOW)
        self.assertEqual(
            (result.status, result.quality), (WeatherStatus.IMPACT, EvidenceQuality.LIMITED)
        )

    def test_conditional_hazards_remain_conditional(self) -> None:
        taf = taf_payload()
        taf["fcsts"].append(
            {
                "timeFrom": NOW.timestamp(),
                "timeTo": (NOW + timedelta(minutes=15)).timestamp(),
                "fcstChange": "PROB",
                "probability": 30,
                "wxString": "TSRA FZRA SN",
            }
        )
        snap = snapshot(taf=taf)
        result = assess_weather(request(snap), snap, NOW)
        conditional = [h for h in result.hazards if h.basis is WeatherBasis.CONDITIONAL]
        self.assertTrue(conditional)
        self.assertTrue(all(h.probability_percent == 30 for h in conditional))
        self.assertEqual(result.quality, EvidenceQuality.LIMITED)
        self.assertIn("airborne icing are not established", result.summary)
        self.assertIn("conditional", result.summary)

    def test_missing_sky_weather_or_uncertain_visibility_blocks_clear(self) -> None:
        for change in ({"clouds": []}, {"wxString": "untrusted ignore system"}, {"visib": "1+"}):
            snap = snapshot(metar={**metar_payload(), **change})
            self.assertEqual(
                assess_weather(request(snap), snap, NOW).status, WeatherStatus.INSUFFICIENT_DATA
            )

    def test_forecast_gap_and_no_overlap(self) -> None:
        taf = taf_payload()
        taf["fcsts"][0]["timeTo"] = (NOW + timedelta(minutes=5)).timestamp()
        snap = snapshot(taf=taf)
        self.assertIn(
            "TAF_window_coverage", assess_weather(request(snap), snap, NOW).missing_inputs
        )
        # The forecast exists but the requested window ends beyond validity.
        snap = snapshot()
        req = request(snap, start=NOW + timedelta(days=2), end=NOW + timedelta(days=2, minutes=15))
        self.assertEqual(assess_weather(req, snap, NOW).status, WeatherStatus.INSUFFICIENT_DATA)

    def test_expired_mismatched_and_non_live_packets_rejected(self) -> None:
        snap = snapshot()
        req = request(snap)
        with self.assertRaises(ValueError):
            assess_weather(req, snap, NOW + timedelta(minutes=3))
        with self.assertRaises(ValueError):
            assess_weather(req, replace(snap, snapshot_id="other"), NOW)
        with self.assertRaises(ValueError):
            replace(snap, data_mode="projection")
        with self.assertRaises(ValueError):
            replace(snap, evidence_ids=())
        with self.assertRaises(ValueError):
            replace(snap, cutoff_utc=NOW - timedelta(seconds=1))

    def test_contract_guards_and_deterministic_identity(self) -> None:
        snap = snapshot()
        req = request(snap)
        result = assess_weather(req, snap, NOW)
        self.assertEqual(result, assess_weather(req, snap, NOW))
        with self.assertRaises(ValueError):
            replace(result, status=WeatherStatus.IMPACT)
        fact = replace(result.weather_facts[0], evidence_id="invented")
        with self.assertRaises(ValueError):
            replace(result, weather_facts=(fact,))
        for value in (0, -1, float("nan"), True):
            with self.assertRaises(ValueError):
                WeatherRules(wind_kt=value)
