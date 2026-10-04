"""Scoped AWC JSON adapter; no polling, database writes or synthetic fallback.

Field mapping: https://aviationweather.gov/data/schema/openapi.yaml (2026-10-04).
Source strings are untrusted data, never instructions.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from fractions import Fraction
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from shared.contracts import (
    ForecastGroup,
    ForecastTemperature,
    MetarEvidence,
    TafEvidence,
    VisibilityBound,
    WeatherConditions,
    weather_utc,
)

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_RECORDS = 400
STATUTE_MILE_M = 1609.344
type Product = Literal["metar", "taf"]


class AwcError(ValueError):
    """Safe diagnostic without raw strings, provider bodies or response headers."""

    def __init__(self, category: str, retry_after_s: float | None = None) -> None:
        self.category = category
        self.retry_after_s = retry_after_s
        super().__init__(category)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes
    retry_after: str | None = None


type Transport = Callable[[str, Mapping[str, str], float], HttpResponse]


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(
        self, req: Request, fp: object, code: int, msg: str, headers: object, newurl: str
    ) -> None:
        return None


def http_get(url: str, headers: Mapping[str, str], timeout_s: float) -> HttpResponse:
    request = Request(url, headers=dict(headers), method="GET")
    try:
        with build_opener(_NoRedirect).open(request, timeout=timeout_s) as response:
            return HttpResponse(response.status, response.read(MAX_RESPONSE_BYTES + 1))
    except HTTPError as error:
        result = HttpResponse(error.code, b"", error.headers.get("Retry-After"))
        error.close()
        return result
    except (URLError, TimeoutError, OSError):
        raise AwcError("connection or timeout", 5.0) from None


def product_url(product: Product, base_url: str = "https://aviationweather.gov/api/data") -> str:
    parts = urlsplit(base_url)
    if product not in {"metar", "taf"} or (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
    ):
        raise ValueError("AWC requires an HTTPS base URL and METAR/TAF product")
    return f"{base_url.rstrip('/')}/{product}?ids=KDTW&format=json"


def metar_url(base_url: str = "https://aviationweather.gov/api/data") -> str:
    return product_url("metar", base_url)


def taf_url(base_url: str = "https://aviationweather.gov/api/data") -> str:
    return product_url("taf", base_url)


def _number(value: object, minimum: float = 0, maximum: float = 100000) -> float | None:
    if type(value) not in (int, float):
        return None
    number = float(value)  # type: ignore[arg-type]
    return number if math.isfinite(number) and minimum <= number <= maximum else None


def _time(value: object) -> datetime:
    try:
        if type(value) in (int, float):
            result = datetime.fromtimestamp(float(value), UTC)  # type: ignore[arg-type]
        elif isinstance(value, str):
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            raise ValueError
        weather_utc(result)
        return result
    except (ValueError, OverflowError, OSError):
        raise AwcError("invalid weather timestamp") from None


def _visibility(value: object) -> tuple[float | None, VisibilityBound]:
    bound = VisibilityBound.EXACT
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None, bound
        if text.endswith("+"):
            bound, text = VisibilityBound.GREATER_THAN, text[:-1]
        elif text.startswith("P"):
            bound, text = VisibilityBound.GREATER_THAN, text[1:]
        elif text.startswith("M"):
            bound, text = VisibilityBound.LESS_THAN, text[1:]
        try:
            value = sum(float(Fraction(part)) for part in text.split())
        except (ValueError, ZeroDivisionError, OverflowError):
            return None, bound
    number = _number(value, 0, 100)
    return (number * STATUTE_MILE_M if number is not None else None), bound


_WX_CODE = re.compile(
    r"^[+-]?(?:VC)?(?:MI|PR|BC|DR|BL|SH|TS|FZ)?"
    r"(?:(?:DZ|RA|SN|SG|IC|PL|GR|GS|UP|BR|FG|FU|VA|DU|SA|HZ|PY|PO|SQ|FC|SS|DS))+$"
)


def parse_conditions(record: Mapping[str, object]) -> WeatherConditions:
    """JSON null wxString denotes decoded no-weather; an absent key is unknown."""
    missing: list[str] = []
    direction = _number(record.get("wdir"), 0, 360)
    variable = record.get("wdir") == "VRB"
    speed, gust = _number(record.get("wspd"), 0, 300), _number(record.get("wgst"), 0, 300)
    visibility, bound = _visibility(record.get("visib"))
    if speed is None:
        missing.append("wind_speed")
        if record.get("wspd") is not None:
            missing.append("invalid_wind_speed")
    if direction is None and not variable and speed != 0:
        missing.append("wind_direction")
    if visibility is None:
        missing.append("visibility")
        if record.get("visib") is not None:
            missing.append("invalid_visibility")
    ceilings: list[float] = []
    vertical = _number(record.get("vertVis"))
    if vertical is not None:
        ceilings.append(vertical)
    clouds = record.get("clouds")
    sky_known = vertical is not None
    clear = False
    invalid_sky = clouds is not None and not isinstance(clouds, list)
    if isinstance(clouds, list) and clouds:
        sky_known = True
        covers: list[str] = []
        for layer in clouds:
            if not isinstance(layer, dict) or layer.get("cover") not in {
                "CLR",
                "SKC",
                "CAVOK",
                "NSC",
                "FEW",
                "SCT",
                "BKN",
                "OVC",
                "OVX",
                "VV",
            }:
                invalid_sky = True
                continue
            cover = str(layer["cover"])
            covers.append(cover)
            if cover in {"BKN", "OVC", "OVX", "VV"}:
                base = _number(layer.get("base"))
                if base is not None:
                    ceilings.append(base)
                elif vertical is None:
                    invalid_sky = True
        clear = bool(covers) and all(c in {"CLR", "SKC", "CAVOK", "NSC"} for c in covers)
    sky_known = sky_known and not invalid_sky
    if not sky_known:
        missing.append("sky")
    if invalid_sky:
        missing.append("invalid_sky")
    wx = record.get("wxString")
    weather_known = "wxString" in record and (wx is None or isinstance(wx, str))
    codes = tuple(wx.split()) if isinstance(wx, str) else ()
    if codes == ("NSW",):
        codes = ()
    if any(not _WX_CODE.fullmatch(code) and code not in {"TS", "VCTS"} for code in codes):
        weather_known = False
        codes = ()
        missing.append("invalid_weather_codes")
    if record.get("notDecoded"):
        weather_known = False
        missing.append("invalid_weather_codes")
    if not weather_known:
        missing.append("weather_codes")
    return WeatherConditions(
        wind_direction_deg=direction,
        wind_variable=variable,
        wind_speed_kt=speed,
        wind_gust_kt=gust,
        visibility_m=visibility,
        visibility_bound=bound,
        ceiling_ft=min(ceilings) if ceilings else None,
        sky_known=sky_known,
        clear_skies=clear and sky_known and not ceilings,
        weather_codes=codes,
        weather_known=weather_known,
        temperature_c=_number(record.get("temp"), -100, 100),
        dewpoint_c=_number(record.get("dewp"), -100, 100),
        missing_fields=tuple(missing),
    )


def _records(payload: object) -> list[dict[str, object]]:
    if not isinstance(payload, list) or len(payload) > MAX_RECORDS:
        raise AwcError("invalid weather response")
    records: list[dict[str, object]] = []
    for record in payload:
        if not isinstance(record, dict) or record.get("icaoId") != "KDTW":
            raise AwcError("invalid station record")
        records.append(record)
    return records


def _raw(record: Mapping[str, object], field: str) -> str:
    raw = record.get(field)
    if not isinstance(raw, str) or len(raw) > 32768:
        raise AwcError("invalid source text")
    return raw


def _id(product: str, record: object) -> str:
    data = json.dumps(record, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return f"awc-{product}-{hashlib.sha256(data.encode()).hexdigest()}"


def _metar_weather(raw: str, observed: datetime) -> tuple[str, ...] | None:
    """Read the weather group of a complete US METAR when JSON omits wxString.

    AWC documents the body order at https://aviationweather.gov/help/data/#metar.
    Require the surrounding groups; missing, abridged, sensor-failed or unparsed
    bodies stay unknown. Remarks are never interpreted as current weather.
    """
    tokens = raw.rstrip("=").split()
    if any(t in {"TSNO", "FZRANO", "PNO"} for t in tokens):
        return None
    if "RMK" in tokens:
        tokens = tokens[: tokens.index("RMK")]
    if tokens and tokens[0] in {"METAR", "SPECI"}:
        tokens = tokens[1:]
    if len(tokens) < 6 or tokens[0] != "KDTW" or tokens[1] != observed.strftime("%d%H%MZ"):
        return None
    tokens = tokens[2:]
    if tokens and tokens[0] in {"AUTO", "COR"}:
        tokens = tokens[1:]
    if not tokens or not re.fullmatch(r"(?:VRB|\d{3})\d{2,3}(?:G\d{2,3})?KT", tokens[0]):
        return None
    tokens = tokens[1:]
    if tokens and re.fullmatch(r"\d{3}V\d{3}", tokens[0]):
        tokens = tokens[1:]
    if len(tokens) > 1 and re.fullmatch(r"\d+", tokens[0]) and "/" in tokens[1]:
        tokens = tokens[1:]
    if not tokens or not re.fullmatch(r"[MP]?(?:\d+|\d+/\d+)SM", tokens[0]):
        return None
    tokens = tokens[1:]
    while tokens and re.fullmatch(r"R\d{2}[LRC]?/[MP]?\d{4}(?:V[MP]?\d{4})?FT", tokens[0]):
        tokens = tokens[1:]
    weather: list[str] = []
    while tokens and (_WX_CODE.fullmatch(tokens[0]) or tokens[0] in {"TS", "VCTS"}):
        weather.append(tokens.pop(0))
    if len(weather) > 3:
        return None
    sky = 0
    while tokens and re.fullmatch(
        r"(?:CLR|SKC|VV\d{3}|(?:FEW|SCT|BKN|OVC)\d{3}(?:CB|TCU)?)", tokens[0]
    ):
        sky += 1
        tokens = tokens[1:]
    if (
        not sky
        or len(tokens) != 2
        or not re.fullmatch(r"M?\d{2}/M?\d{2}", tokens[0])
        or not re.fullmatch(r"A\d{4}", tokens[1])
    ):
        return None
    return tuple(weather)


def parse_metar_response(
    payload: object,
    fetched_at_utc: datetime,
    source_ref: str = "https://aviationweather.gov/api/data/metar?ids=KDTW&format=json",
) -> tuple[MetarEvidence, ...]:
    weather_utc(fetched_at_utc)
    results: dict[datetime, MetarEvidence] = {}
    for record in _records(payload):
        observed = _time(record.get("obsTime"))
        raw = _raw(record, "rawOb")
        decoded = dict(record)
        if "wxString" not in decoded and not decoded.get("notDecoded"):
            codes = _metar_weather(raw, observed)
            if codes is not None:
                decoded["wxString"] = " ".join(codes)
        conditions = parse_conditions(decoded)
        item = MetarEvidence(
            evidence_id=_id("metar", [observed.isoformat(), raw, asdict(conditions)]),
            observed_at_utc=observed,
            fetched_at_utc=fetched_at_utc,
            source_ref=source_ref,
            raw_text=raw,
            conditions=conditions,
        )
        if observed in results and results[observed] != item:
            raise AwcError("conflicting METAR version")
        results[observed] = item
    return tuple(results[t] for t in sorted(results))


def parse_taf_response(
    payload: object,
    fetched_at_utc: datetime,
    source_ref: str = "https://aviationweather.gov/api/data/taf?ids=KDTW&format=json",
) -> tuple[TafEvidence, ...]:
    weather_utc(fetched_at_utc)
    results: dict[tuple[datetime, str], TafEvidence] = {}
    for record in _records(payload):
        issued, start, end = (
            _time(record.get(name)) for name in ("issueTime", "validTimeFrom", "validTimeTo")
        )
        raw = _raw(record, "rawTAF")
        amendment = (
            "amended"
            if re.search(r"\bAMD\b", raw)
            else ("corrected" if re.search(r"\bCOR\b", raw) else "original")
        )
        forecasts = record.get("fcsts")
        if not isinstance(forecasts, list) or len(forecasts) > 100:
            raise AwcError("invalid forecast groups")
        groups: list[ForecastGroup] = []
        temperatures: list[ForecastTemperature] = []
        for forecast in forecasts:
            if not isinstance(forecast, dict):
                raise AwcError("invalid forecast group")
            change = forecast.get("fcstChange") or "BASE"
            probability = forecast.get("probability")
            if probability is not None and type(probability) is not int:
                raise AwcError("invalid forecast probability")
            if not isinstance(change, str):
                raise AwcError("invalid forecast change")
            transition = forecast.get("timeBec")
            conditions_record = dict(forecast)
            if change in {"TEMPO", "PROB", "BECMG"} and forecast.get("wxString") is None:
                conditions_record.pop("wxString", None)
            temp_entries = forecast.get("temp")
            if temp_entries is not None:
                if not isinstance(temp_entries, list):
                    raise AwcError("invalid forecast temperatures")
                for entry in temp_entries:
                    if not isinstance(entry, dict):
                        raise AwcError("invalid forecast temperature")
                    temperature = _number(entry.get("sfcTemp"), -100, 100)
                    kind = entry.get("maxOrMin")
                    if temperature is None or not isinstance(kind, str):
                        raise AwcError("invalid forecast temperature")
                    temperatures.append(
                        ForecastTemperature(_time(entry.get("validTime")), temperature, kind)
                    )
            groups.append(
                ForecastGroup(
                    _time(forecast.get("timeFrom")),
                    _time(forecast.get("timeTo")),
                    parse_conditions(conditions_record),
                    change,
                    probability,
                    _time(transition) if transition is not None else None,
                )
            )
        groups.sort(key=lambda g: (g.start_utc, g.change))
        item = TafEvidence(
            evidence_id=_id("taf", [issued.isoformat(), raw, record["fcsts"]]),
            issued_at_utc=issued,
            fetched_at_utc=fetched_at_utc,
            start_utc=start,
            end_utc=end,
            source_ref=source_ref,
            raw_text=raw,
            groups=tuple(groups),
            amendment=amendment,
            temperatures=tuple(temperatures),
        )
        key = (issued, amendment)
        if key in results and results[key] != item:
            raise AwcError("conflicting TAF version")
        results[key] = item
    return tuple(results[t] for t in sorted(results))


@dataclass(frozen=True, slots=True)
class FetchResult:
    product: Product
    fetched_at_utc: datetime
    records: tuple[MetarEvidence, ...] | tuple[TafEvidence, ...]


class AwcClient:
    def __init__(
        self,
        user_agent: str,
        *,
        base_url: str = "https://aviationweather.gov/api/data",
        transport: Transport = http_get,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not user_agent.strip() or "<" in user_agent or any(ord(c) < 32 for c in user_agent):
            raise ValueError("DATA_USER_AGENT must identify the team")
        product_url("metar", base_url)
        self._user_agent, self._base, self._transport = user_agent, base_url, transport
        self._clock = clock or (lambda: datetime.now(UTC))

    async def fetch(self, product: Product) -> FetchResult:
        url = product_url(product, self._base)
        try:
            async with asyncio.timeout(5):
                response = await asyncio.to_thread(
                    self._transport,
                    url,
                    {"User-Agent": self._user_agent, "Accept": "application/json"},
                    5.0,
                )
        except AwcError:
            raise
        except Exception:
            raise AwcError("connection or timeout", 5.0) from None
        now = self._clock()
        weather_utc(now)
        if response.status == 204:
            return FetchResult(product, now, ())
        if response.status != 200:
            retry: float | None = None
            if response.status == 429 or response.status >= 500:
                retry = 5.0
                if response.retry_after:
                    try:
                        retry = float(response.retry_after)
                    except ValueError:
                        try:
                            retry = (
                                parsedate_to_datetime(response.retry_after) - now
                            ).total_seconds()
                        except (ValueError, TypeError, OverflowError):
                            pass
                retry = max(0.0, retry) if math.isfinite(retry) else 5.0
            raise AwcError("rate limited" if response.status == 429 else "request failed", retry)
        if len(response.body) > MAX_RESPONSE_BYTES:
            raise AwcError("response too large")
        try:
            payload = json.loads(response.body)
            records = (
                parse_metar_response(payload, now, url)
                if product == "metar"
                else parse_taf_response(payload, now, url)
            )
        except (ValueError, TypeError, KeyError, OverflowError):
            raise AwcError("malformed weather response") from None
        return FetchResult(product, now, records)
