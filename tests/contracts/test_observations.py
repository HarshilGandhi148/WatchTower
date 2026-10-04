"""Normalized DTO and HTTP bridge regression tests; no providers or DB required."""

import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from io import BytesIO
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from urllib.error import HTTPError, URLError

from shared.contracts import AircraftBatch, AircraftObservation, SourceFailure, WeatherRecord
from shared.settings import SpacetimeSettings
from shared.spacetimedb_adapter import (
    SpacetimeAdapter,
    SpacetimeError,
    http_transport,
    payload_json,
)

AT = datetime(2026, 10, 4, tzinfo=UTC)


def record() -> WeatherRecord:
    return WeatherRecord(observed_at_utc=AT, fetched_at_utc=AT, source_ref="https://example.org")


class ObservationContractTests(TestCase):
    def test_nulls_and_zero_are_different_and_times_are_utc(self) -> None:
        data = json.loads(payload_json(replace(record(), wind_speed_kt=0)))
        self.assertEqual(data["wind_speed_kt"], 0)
        self.assertIsNone(data["visibility_m"])
        self.assertEqual(data["observed_at_utc"], "2026-10-04T00:00:00.000Z")
        self.assertEqual(data["data_mode"], "live")

    def test_invalid_weather_inputs_are_rejected(self) -> None:
        for change in (
            {"observed_at_utc": AT.replace(tzinfo=None)},
            {"observed_at_utc": AT + timedelta(seconds=1)},
            {"observed_at_utc": AT.replace(microsecond=1)},
            {"airport_icao": "KJFK"},
            {"provider": "fake"},
            {"wind_speed_kt": -1},
            {"wind_speed_kt": float("nan")},
            {"wind_speed_kt": True},
            {"source_ref": ""},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(record(), **change)

    def test_aircraft_identity_coordinates_ground_and_duplicates(self) -> None:
        aircraft = AircraftObservation(aircraft_id="test", observed_at_utc=AT, on_ground=True)
        self.assertIsNone(aircraft.altitude_ft)
        for change in (
            {"aircraft_id": ""},
            {"latitude_deg": 42},
            {"latitude_deg": 91, "longitude_deg": 0},
            {"on_ground": "ground"},
            {"position_age_s": -1},
        ):
            with self.subTest(change=change), self.assertRaises(ValueError):
                replace(aircraft, **change)
        with self.assertRaises(ValueError):
            AircraftBatch(fetched_at_utc=AT, source_ref="ref", observations=(aircraft, aircraft))
        with self.assertRaises(ValueError):
            AircraftBatch(
                fetched_at_utc=AT - timedelta(seconds=1), source_ref="ref", observations=(aircraft,)
            )
        self.assertEqual(
            AircraftBatch(fetched_at_utc=AT, source_ref="ref", observations=()).observations, ()
        )
        self.assertEqual(
            SourceFailure(provider="awc", source_ref="ref", fetched_at_utc=AT).provider, "awc"
        )

    def test_settings_reject_invalid_origins_and_keep_tokens_out_of_repr(self) -> None:
        settings = SpacetimeSettings("http://127.0.0.1:3000", "watchtower-local", "private-token")
        self.assertNotIn("private-token", repr(settings))
        for origin in (
            "",
            "ws://localhost",
            "http://remote.example",
            "https://user:secret@example.org",
            "https://example.org/path",
            "https://example.org?token=x",
        ):
            with self.subTest(origin=origin), self.assertRaises(ValueError):
                SpacetimeSettings(origin, "test", "token")
        with self.assertRaises(ValueError):
            SpacetimeSettings.from_env({})
        with self.assertRaises(ValueError):
            replace(settings, database="test; DROP TABLE")
        with self.assertRaises(ValueError):
            replace(settings, timeout_s=float("nan"))

    def test_transport_errors_are_safe_and_redirects_are_not_followed(self) -> None:
        for error, category in (
            (
                HTTPError("http://localhost", 403, "private-token", {}, BytesIO(b"private-token")),
                "authorization",
            ),
            (
                HTTPError("http://localhost", 400, "private-token", {}, BytesIO(b"private-token")),
                "request rejected",
            ),
            (URLError("private-token"), "connection or timeout"),
            (TimeoutError("private-token"), "connection or timeout"),
        ):
            with self.subTest(category=category), patch("urllib.request.build_opener") as opener:
                opener.return_value.open.side_effect = error
                with self.assertRaises(SpacetimeError) as raised:
                    http_transport("http://localhost", b"[]", {}, 1)
                self.assertEqual(raised.exception.category, category)
                self.assertNotIn("private-token", str(raised.exception))
                redirect = opener.call_args.args[0]()
                self.assertIsNone(
                    redirect.redirect_request(None, None, 302, "", {}, "https://elsewhere")
                )


class AdapterTests(IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.settings = SpacetimeSettings(
            "http://127.0.0.1:3000", "watchtower-local", "private-token"
        )

    async def test_reducer_wire_shape_and_headers(self) -> None:
        calls = []

        def transport(url, body, headers, timeout):
            calls.append((url, body, headers, timeout))
            return b""

        adapter = SpacetimeAdapter(self.settings, transport)
        await adapter.ingest_weather_record(record())
        url, body, headers, timeout = calls[0]
        self.assertTrue(url.endswith("/call/ingest_weather_record"))
        self.assertEqual(headers["Authorization"], "Bearer private-token")
        self.assertEqual(headers["Content-Type"], "application/json")
        self.assertEqual(timeout, 10)
        args = json.loads(body)
        self.assertEqual(len(args), 1)
        self.assertIsInstance(args[0], str)
        self.assertIsNone(json.loads(args[0])["visibility_m"])
        await adapter.ingest_aircraft_batch(
            AircraftBatch(fetched_at_utc=AT, source_ref="ref", observations=())
        )
        await adapter.report_source_failure(
            SourceFailure(provider="awc", source_ref="ref", fetched_at_utc=AT)
        )
        self.assertEqual(len(calls), 3)

    async def test_bounded_sql_decodes_native_options_and_timestamps(self) -> None:
        result = [
            {
                "schema": {
                    "elements": [
                        {"name": {"some": "wind_speed_kt"}},
                        {"name": {"some": "visibility_m"}},
                        {"name": {"some": "observed_at_utc"}},
                        {"name": {"some": "on_ground"}},
                    ]
                },
                "rows": [[[0, 0.0], [1, []], [0], [0, True]]],
            }
        ]

        def transport(url, body, headers, timeout):
            self.assertTrue(url.endswith("/sql"))
            self.assertEqual(body, b"SELECT * FROM weather_records LIMIT 1001")
            return json.dumps(result).encode()

        rows = await SpacetimeAdapter(self.settings, transport).read_observations("weather_records")
        self.assertEqual(
            rows,
            [
                {
                    "wind_speed_kt": 0,
                    "visibility_m": None,
                    "observed_at_utc": datetime(1970, 1, 1, tzinfo=UTC),
                    "on_ground": True,
                }
            ],
        )
        with self.assertRaises(ValueError):
            await SpacetimeAdapter(self.settings, transport).read_observations(
                "weather_records; DROP TABLE"
            )

    async def test_response_and_transport_failures_are_safe(self) -> None:
        for data in (
            b"not json",
            b"[]",
            b"[{}]",
            b'[{"schema":{"elements":[]},"rows":[[1]]}]',
            b'[{"schema":{"elements":[{"name":{"some":"wind_speed_kt"}}]},"rows":[[[9,0]]]}]',
        ):
            with self.subTest(data=data), self.assertRaises(SpacetimeError):
                await SpacetimeAdapter(
                    self.settings, lambda *args, response=data: response
                ).read_observations("weather_records")

        def failing(*args):
            raise RuntimeError("private-token")

        with self.assertRaises(SpacetimeError) as raised:
            await SpacetimeAdapter(self.settings, failing).ingest_weather_record(record())
        self.assertNotIn("private-token", str(raised.exception))
