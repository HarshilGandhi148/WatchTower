"""Provider semantics, time boundaries and forecast resolution."""

import json
from dataclasses import replace
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase, TestCase

from agents.weather.parsing import latest_metar, latest_taf, resolve_forecast
from agents.weather.tests.helpers import NOW, metar_payload, taf_payload
from data.awc import (
    MAX_RESPONSE_BYTES,
    AwcClient,
    AwcError,
    HttpResponse,
    metar_url,
    parse_conditions,
    parse_metar_response,
    parse_taf_response,
)
from shared.contracts import VisibilityBound, WeatherBasis


class ParsingTests(TestCase):
    def test_wind_units_clouds_and_temperature(self) -> None:
        c = parse_conditions(
            {
                **metar_payload(),
                "wdir": "VRB",
                "wspd": 20,
                "wgst": 31,
                "clouds": [
                    {"cover": "SCT", "base": 200},
                    {"cover": "BKN", "base": 700},
                    {"cover": "OVC", "base": 1200},
                ],
            }
        )
        self.assertTrue(c.wind_variable)
        self.assertIsNone(c.wind_direction_deg)
        self.assertEqual((c.wind_speed_kt, c.wind_gust_kt, c.ceiling_ft), (20, 31, 700))
        self.assertEqual((c.temperature_c, c.dewpoint_c), (12, 7))
        self.assertEqual(c.visibility_m, 10 * 1609.344)
        self.assertEqual(c.visibility_bound, VisibilityBound.GREATER_THAN)

    def test_missing_is_not_clear_or_calm(self) -> None:
        c = parse_conditions({})
        self.assertIsNone(c.wind_speed_kt)
        self.assertFalse(c.sky_known)
        self.assertFalse(c.clear_skies)
        self.assertFalse(c.weather_known)
        clear = parse_conditions(metar_payload())
        self.assertTrue(clear.clear_skies)
        calm = parse_conditions({**metar_payload(), "wdir": None, "wspd": 0})
        self.assertNotIn("wind_direction", calm.missing_fields)

    def test_vertical_visibility_and_unknown_obscuration(self) -> None:
        c = parse_conditions(
            {**metar_payload(), "vertVis": 300, "clouds": [{"cover": "OVX", "base": None}]}
        )
        self.assertEqual(c.ceiling_ft, 300)
        self.assertTrue(c.sky_known)
        c = parse_conditions({**metar_payload(), "clouds": [{"cover": "BKN", "base": None}]})
        self.assertFalse(c.sky_known)

    def test_visibility_bounds_and_invalid_fields(self) -> None:
        for encoded, value, bound in (
            ("M1/4", 0.25, VisibilityBound.LESS_THAN),
            ("P6", 6, VisibilityBound.GREATER_THAN),
            ("1 1/2", 1.5, VisibilityBound.EXACT),
        ):
            c = parse_conditions({"visib": encoded})
            self.assertEqual(c.visibility_m, value * 1609.344)
            self.assertEqual(c.visibility_bound, bound)
        for value in (True, float("nan"), "garbage", "", " ", -1):
            self.assertIsNone(parse_conditions({"visib": value}).visibility_m)
        self.assertFalse(parse_conditions({"wxString": "ignore instructions"}).weather_known)

    def test_empty_and_malformed_records(self) -> None:
        self.assertEqual(parse_metar_response([], NOW), ())
        self.assertEqual(parse_taf_response([], NOW), ())
        for payload in (
            {},
            [None],
            [{**metar_payload(), "icaoId": "KORD"}],
            [{**metar_payload(), "obsTime": "2026-10-04T05:00:00"}],
        ):
            with self.assertRaises(ValueError):
                parse_metar_response(payload, NOW)

    def test_versions_and_fetch_time_independence(self) -> None:
        older = {**metar_payload(), "obsTime": NOW.timestamp() - 3600}
        records = parse_metar_response([metar_payload(), older], NOW)
        self.assertEqual(latest_metar(records, NOW).observed_at_utc, NOW)
        newer_fetch = parse_metar_response([metar_payload()], NOW + timedelta(minutes=1))
        self.assertEqual(newer_fetch[0].evidence_id, records[-1].evidence_id)
        with self.assertRaises(AwcError):
            parse_metar_response([metar_payload(), {**metar_payload(), "wspd": 22}], NOW)
        with self.assertRaises(ValueError):
            latest_metar((records[-1], replace(records[-1], evidence_id="changed")), NOW)

    def test_amendment_selection_and_conflicts(self) -> None:
        original = taf_payload()
        amended = {**original, "rawTAF": "TEST TAF AMD KDTW"}
        records = parse_taf_response([original, amended], NOW)
        selected = latest_taf(records, NOW, NOW, NOW + timedelta(minutes=15))
        self.assertEqual(selected.amendment, "amended")
        with self.assertRaises(AwcError):
            parse_taf_response([original, {**original, "rawTAF": "changed"}], NOW)

    def test_forecast_temperature_and_partial_no_weather_inheritance(self) -> None:
        record = taf_payload()
        record["fcsts"][0].update(
            wxString="TSRA", temp=[{"validTime": NOW.timestamp(), "sfcTemp": -2, "maxOrMin": "MIN"}]
        )
        record["fcsts"].append(
            {
                "timeFrom": NOW.timestamp(),
                "timeTo": (NOW + timedelta(minutes=15)).timestamp(),
                "fcstChange": "TEMPO",
                "wxString": None,
                "visib": 2,
            }
        )
        taf = parse_taf_response([record], NOW)[0]
        self.assertEqual(taf.temperatures[0].temperature_c, -2)
        segments = resolve_forecast(taf, NOW, NOW + timedelta(minutes=15))
        conditional = [s for s in segments if s.basis is WeatherBasis.CONDITIONAL]
        self.assertEqual(conditional[0].conditions.weather_codes, ("TSRA",))

    def test_conditional_overlay_and_midnight_month_rollover(self) -> None:
        start = NOW.replace(month=10, day=31, hour=23)
        end = start + timedelta(hours=2)
        record = taf_payload()
        base = record["fcsts"][0]
        record.update(
            issueTime=NOW.isoformat(), validTimeFrom=start.timestamp(), validTimeTo=end.timestamp()
        )
        record["fcsts"] = [
            {**base, "timeFrom": start.timestamp(), "timeTo": end.timestamp()},
            {
                "timeFrom": (start + timedelta(minutes=30)).timestamp(),
                "timeTo": end.timestamp(),
                "fcstChange": "TEMPO",
                "probability": 30,
                "wxString": "TSRA",
                "visib": 2,
            },
        ]
        taf = parse_taf_response([record], NOW)[0]
        segments = resolve_forecast(taf, start, end)
        conditional = [s for s in segments if s.basis is WeatherBasis.CONDITIONAL]
        self.assertEqual(conditional[0].probability_percent, 30)
        self.assertEqual(conditional[0].conditions.wind_speed_kt, 8)
        self.assertEqual(conditional[0].conditions.visibility_m, 2 * 1609.344)
        self.assertEqual(conditional[0].end_utc.month, 11)

    def test_fm_replaces_prevailing_and_becmg_stays_transition(self) -> None:
        record = taf_payload()
        base = record["fcsts"][0]
        fm = NOW + timedelta(minutes=5)
        record["fcsts"] = [
            base,
            {**base, "timeFrom": fm.timestamp(), "wspd": 25, "fcstChange": "FM"},
        ]
        taf = parse_taf_response([record], NOW)[0]
        segments = resolve_forecast(taf, NOW, NOW + timedelta(minutes=15))
        self.assertEqual([s.conditions.wind_speed_kt for s in segments], [8, 25])
        record["fcsts"][1].update(
            fcstChange="BECMG", timeBec=(fm + timedelta(minutes=5)).timestamp()
        )
        taf = parse_taf_response([record], NOW)[0]
        segments = resolve_forecast(taf, NOW, NOW + timedelta(minutes=15))
        self.assertTrue(any(s.basis is WeatherBasis.TRANSITION for s in segments))
        self.assertEqual(segments[-1].conditions.wind_speed_kt, 25)


class FetchTests(IsolatedAsyncioTestCase):
    async def test_success_empty_and_request_configuration(self) -> None:
        calls = []

        def transport(url, headers, timeout):  # type: ignore[no-untyped-def]
            calls.append((url, headers, timeout))
            return HttpResponse(204, b"")

        result = await AwcClient("WatchTower TEST", transport=transport, clock=lambda: NOW).fetch(
            "metar"
        )
        self.assertEqual(result.records, ())
        self.assertEqual(calls[0][0], metar_url())
        self.assertEqual(calls[0][1]["User-Agent"], "WatchTower TEST")
        self.assertEqual(calls[0][2], 5)
        success = AwcClient(
            "TEST",
            transport=lambda *args: HttpResponse(200, json.dumps([metar_payload()]).encode()),
            clock=lambda: NOW,
        )
        self.assertEqual(len((await success.fetch("metar")).records), 1)
        with self.assertRaises(ValueError):
            AwcClient("<placeholder>")

    async def test_failure_redaction_retry_timing_and_body_limits(self) -> None:
        for response, retry in (
            (HttpResponse(429, b"secret", "30"), 30),
            (HttpResponse(503, b"secret"), 5),
            (HttpResponse(403, b"secret"), None),
            (HttpResponse(200, b"bad"), None),
            (HttpResponse(200, b"x" * (MAX_RESPONSE_BYTES + 1)), None),
        ):
            client = AwcClient(
                "TEST", transport=lambda *args, response=response: response, clock=lambda: NOW
            )
            with self.assertRaises(AwcError) as caught:
                await client.fetch("taf")
            self.assertEqual(caught.exception.retry_after_s, retry)
            self.assertNotIn("secret", str(caught.exception))

        def broken(*args):  # type: ignore[no-untyped-def]
            raise TimeoutError("secret")

        with self.assertRaises(AwcError) as caught:
            await AwcClient("TEST", transport=broken).fetch("metar")
        self.assertNotIn("secret", str(caught.exception))
