"""Bounded HTTP bridge for normalized observations, using SpacetimeDB 2.10.2."""

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from http.client import HTTPMessage
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request

from shared.contracts import AircraftBatch, SourceFailure, WeatherRecord, _utc_millis
from shared.settings import SpacetimeSettings

type ObservationTable = Literal["weather_records", "aircraft_observations", "source_health"]
type Transport = Callable[[str, bytes, Mapping[str, str], float], bytes]
type Payload = WeatherRecord | AircraftBatch | SourceFailure
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


class SpacetimeError(RuntimeError):
    """Safe error category; no provider/database body or credentials are included."""

    def __init__(self, category: str, status: int | None = None) -> None:
        self.category = category
        self.status = status
        super().__init__(category if status is None else f"{category} (HTTP {status})")


def http_transport(url: str, body: bytes, headers: Mapping[str, str], timeout: float) -> bytes:
    # Do not forward bearer tokens through redirects.
    from urllib.request import HTTPRedirectHandler, build_opener

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(
            self,
            req: Request,
            fp: object,
            code: int,
            msg: str,
            headers: HTTPMessage,
            newurl: str,
        ) -> None:
            return None

    request = Request(url, data=body, headers=dict(headers), method="POST")
    try:
        with build_opener(NoRedirect).open(request, timeout=timeout) as response:
            data: bytes = response.read(MAX_RESPONSE_BYTES + 1)
    except HTTPError as error:
        status = error.code
        error.close()
        raise SpacetimeError(
            "authorization" if status in {401, 403} else "request rejected", status
        ) from None
    except (TimeoutError, URLError, OSError):
        raise SpacetimeError("connection or timeout") from None
    if len(data) > MAX_RESPONSE_BYTES:
        raise SpacetimeError("response too large")
    return data


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return _utc_millis(value)
    raise TypeError("Unsupported observation value")


def payload_json(payload: Payload) -> str:
    """Single JSON-string argument avoids ambiguous nullable SATS input encoding."""
    return json.dumps(
        {"schema_version": 1, "data_mode": "live", **asdict(payload)},
        default=_json_default,
        allow_nan=False,
        separators=(",", ":"),
    )


OPTIONAL_NUMBERS = frozenset(
    {
        "wind_direction_deg",
        "wind_speed_kt",
        "wind_gust_kt",
        "visibility_m",
        "ceiling_ft",
        "latitude_deg",
        "longitude_deg",
        "altitude_ft",
        "speed_kt",
        "track_deg",
        "position_age_s",
    }
)
OPTIONAL_TIMES = frozenset({"source_at_utc", "last_success_at_utc"})


def _decode_column(name: str, value: object) -> object:
    # SATS options are tagged tuples: [0, value] / [1, []]. Native timestamps
    # are single-element products containing integer microseconds since epoch.
    if name in OPTIONAL_NUMBERS or name in OPTIONAL_TIMES or name == "on_ground":
        if not isinstance(value, list) or len(value) != 2 or type(value[0]) is not int:
            raise ValueError("Malformed option")
        if value[0] == 1 and value[1] == []:
            return None
        if value[0] != 0:
            raise ValueError("Malformed option tag")
        value = value[1]
        if name == "on_ground" and type(value) is not bool:
            raise ValueError("Malformed boolean")
        if name in OPTIONAL_NUMBERS:
            import math

            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
            ):
                raise ValueError("Malformed measurement")
    if name.endswith("_at_utc"):
        if not isinstance(value, list) or len(value) != 1 or type(value[0]) is not int:
            raise ValueError("Malformed timestamp")
        return datetime(1970, 1, 1, tzinfo=UTC) + timedelta(microseconds=value[0])
    return value


class SpacetimeAdapter:
    def __init__(self, settings: SpacetimeSettings, transport: Transport = http_transport) -> None:
        self._settings = settings
        self._transport = transport

    def _post(self, route: str, body: bytes, content_type: str) -> bytes:
        url = f"{self._settings.http_url.rstrip('/')}/v1/database/{self._settings.database}/{route}"
        try:
            return self._transport(
                url,
                body,
                {
                    "Authorization": f"Bearer {self._settings.ingest_token}",
                    "Content-Type": content_type,
                },
                self._settings.timeout_s,
            )
        except SpacetimeError:
            raise
        except Exception:
            # Injected transports also cannot expose raw exception messages.
            raise SpacetimeError("transport failed") from None

    async def _write(self, reducer: str, payload: Payload) -> None:
        body = json.dumps([payload_json(payload)]).encode()
        await asyncio.to_thread(self._post, f"call/{reducer}", body, "application/json")

    async def ingest_weather_record(self, record: WeatherRecord) -> None:
        await self._write("ingest_weather_record", record)

    async def ingest_aircraft_batch(self, batch: AircraftBatch) -> None:
        await self._write("ingest_aircraft_batch", batch)

    async def report_source_failure(self, failure: SourceFailure) -> None:
        await self._write("report_source_failure", failure)

    async def read_observations(self, table: ObservationTable) -> list[dict[str, object]]:
        if table not in {"weather_records", "aircraft_observations", "source_health"}:
            raise ValueError("Unsupported observation table")
        data = await asyncio.to_thread(
            self._post, "sql", f"SELECT * FROM {table} LIMIT 1000".encode(), "text/plain"
        )
        try:
            results = json.loads(data)
            if not isinstance(results, list) or len(results) != 1:
                raise ValueError
            result = results[0]
            columns = [element["name"]["some"] for element in result["schema"]["elements"]]
            rows = result["rows"]
            if (
                not all(isinstance(name, str) for name in columns)
                or len(columns) != len(set(columns))
                or not isinstance(rows, list)
                or len(rows) > 1000
                or any(not isinstance(row, list) or len(row) != len(columns) for row in rows)
            ):
                raise ValueError
            return [
                {
                    name: _decode_column(name, value)
                    for name, value in zip(columns, row, strict=True)
                }
                for row in rows
            ]
        except (ValueError, KeyError, TypeError, IndexError, OverflowError):
            raise SpacetimeError("invalid SQL response") from None
