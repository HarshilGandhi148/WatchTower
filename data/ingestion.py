"""Independent public ADS-B/FAA polling into SpacetimeDB (infrastructure).

Paid data polling is deliberately absent. Network and persistence errors are redacted;
each source has its own deadline/backoff and successful empty batches are retained.
"""

import argparse
import asyncio
import json
import logging
import os
import random
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.client import HTTPMessage
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from data.adsb import parse_point_response, point_url
from data.faa_status import parse_status_xml
from shared.contracts import SourceFailure
from shared.settings import AirportConfig, SpacetimeSettings
from shared.spacetimedb_adapter import SpacetimeAdapter, SpacetimeError

MAX_BODY = 2_000_000
TIMEOUT_S = 10
Get = Callable[[str, str], bytes]
LOGGER = logging.getLogger("watchtower.ingestion")
ENV_NAMES = frozenset(
    {
        "APP_MODE",
        "AIRPORT_ICAO",
        "AIRPORT_LATITUDE_DEG",
        "AIRPORT_LONGITUDE_DEG",
        "TRAFFIC_RADIUS_NM",
        "SPACETIMEDB_HTTP_URL",
        "SPACETIMEDB_DATABASE",
        "SPACETIMEDB_INGEST_TOKEN",
        "ADSB_BASE_URL",
        "FAA_STATUS_URL",
        "DATA_USER_AGENT",
    }
)


def read_environment(path: Path | None, environ: Mapping[str, str]) -> dict[str, str]:
    """Load explicit ingestion settings without shell expansion or secret diagnostics."""
    settings: dict[str, str] = {}
    if path is not None:
        raw = path.read_bytes()
        if len(raw) > 65536:
            raise ValueError("Environment file is too large")
        for line in raw.decode("utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            name, separator, value = line.partition("=")
            if not separator:
                raise ValueError("Environment file requires NAME=value lines")
            name, value = name.strip(), value.strip()
            if name not in ENV_NAMES:
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            settings[name] = value
    settings.update({name: value for name, value in environ.items() if name in ENV_NAMES})
    return settings


class FeedError(RuntimeError):
    def __init__(self, retry_after_s: float = 0) -> None:
        self.retry_after_s = retry_after_s
        super().__init__("provider fetch failed")


def http_get(url: str, user_agent: str) -> bytes:
    if not url.startswith("https://"):
        raise FeedError

    class NoRedirect(HTTPRedirectHandler):
        def redirect_request(
            self, req: Request, fp: object, code: int, msg: str, headers: HTTPMessage, newurl: str
        ) -> None:
            return None

    try:
        with build_opener(NoRedirect).open(
            Request(url, headers={"User-Agent": user_agent}), timeout=TIMEOUT_S
        ) as response:
            body = response.read(MAX_BODY + 1)
    except HTTPError as error:
        # Only parse the permitted retry delay, never retain/log headers or body.
        retry = error.headers.get("Retry-After", "0")
        error.close()
        try:
            delay = min(300.0, max(0.0, float(retry)))
        except ValueError:
            try:
                until = parsedate_to_datetime(retry)
                if until.utcoffset() is None:
                    until = until.replace(tzinfo=UTC)
                delay = min(300.0, max(0.0, (until - datetime.now(UTC)).total_seconds()))
            except (TypeError, ValueError, OverflowError):
                delay = 0.0
        raise FeedError(delay) from None
    except Exception:
        raise FeedError from None
    if not isinstance(body, bytes) or len(body) > MAX_BODY:
        raise FeedError
    return body


@dataclass(frozen=True, slots=True)
class IngestionConfig:
    airport: AirportConfig = AirportConfig()
    adsb_base_url: str = "https://api.adsb.lol"
    faa_url: str = "https://nasstatus.faa.gov/api/airport-status-information"
    user_agent: str = "WatchTower/0.1"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> "IngestionConfig":
        ua = env.get("DATA_USER_AGENT", "").strip()
        if not ua or "<" in ua:
            raise ValueError("DATA_USER_AGENT must identify the team")
        return cls(
            AirportConfig.from_env(env),
            env.get("ADSB_BASE_URL", "https://api.adsb.lol"),
            env.get("FAA_STATUS_URL", "https://nasstatus.faa.gov/api/airport-status-information"),
            ua,
        )

    def url(self, provider: str) -> str:
        if provider == "adsb_lol":
            return point_url(
                self.adsb_base_url,
                self.airport.latitude_deg,
                self.airport.longitude_deg,
                self.airport.radius_nm,
            )
        if provider == "faa":
            return self.faa_url
        raise ValueError("Unsupported provider")


@dataclass(slots=True)
class PollState:
    failures: int = 0
    due: datetime | None = None
    pending: dict[str, object] | None = None


class IngestionService:
    def __init__(
        self,
        adapter: SpacetimeAdapter,
        config: IngestionConfig,
        *,
        get: Get = http_get,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        jitter: Callable[[], float] = random.random,
    ) -> None:
        self.adapter, self.config, self.get, self.clock, self.jitter = (
            adapter,
            config,
            get,
            clock,
            jitter,
        )
        self.states = {provider: PollState() for provider in ("adsb_lol", "faa")}
        self.locks = {provider: asyncio.Lock() for provider in self.states}

    async def poll(self, provider: str) -> bool:
        """One injected-clock poll. Failed commits retry the identical batch ID/payload."""
        async with self.locks[provider]:
            state = self.states[provider]
            now = self.clock()
            now = now.replace(microsecond=now.microsecond // 1000 * 1000)
            if state.due is not None and now < state.due:
                return False
            url = self.config.url(provider)
            interval = 10 if provider == "adsb_lol" else 60
            try:
                if state.pending is None:
                    body = await asyncio.to_thread(self.get, url, self.config.user_agent)
                    fetched = self.clock()
                    fetched = fetched.replace(microsecond=fetched.microsecond // 1000 * 1000)
                    flags = (
                        ["surface_coverage_unknown", "nearby_not_airport_demand"]
                        if provider == "adsb_lol"
                        else []
                    )
                    if provider == "adsb_lol":
                        adsb = parse_point_response(json.loads(body), fetched)
                        if adsb.rejected_records or adsb.duplicates_dropped:
                            flags.append("rejected_records")
                        if adsb.reported_total is not None and adsb.reported_total != len(
                            adsb.observations
                        ):
                            flags.append("reported_count_mismatch")
                        if adsb.provider_time_utc is None:
                            flags.append("unparsed_source_time")
                        normalized = asdict(adsb)
                    else:
                        faa = parse_status_xml(body, fetched)
                        if faa.malformed_entries:
                            flags.append("rejected_records")
                        if faa.unrecognized_sections:
                            flags.append("unknown_sections")
                        if faa.source_update_time_utc is None:
                            flags.append("unparsed_source_time")
                        normalized = asdict(faa)
                    state.pending = {
                        "schema_version": 1,
                        "data_mode": "live",
                        "batch_id": f"batch-{uuid4().hex}",
                        "provider": provider,
                        "source_ref": url,
                        "configuration": asdict(self.config.airport),
                        "snapshot": normalized,
                        "coverage_flags": flags,
                    }
                await self.adapter.call("ingest_feed_batch", state.pending)
            except Exception as error:
                state.failures += 1
                delay = min(300, interval * 2 ** min(state.failures, 5)) * (1 + 0.2 * self.jitter())
                if isinstance(error, FeedError):
                    delay = max(delay, error.retry_after_s)
                category = (
                    "provider fetch"
                    if isinstance(error, FeedError)
                    else "database authorization"
                    if isinstance(error, SpacetimeError) and error.category == "authorization"
                    else "database commit"
                    if isinstance(error, SpacetimeError)
                    else "input or ingestion"
                )
                LOGGER.warning(
                    "%s ingestion failed (%s); retrying in %.1f seconds", provider, category, delay
                )
                state.due = now + timedelta(seconds=delay)
                if state.pending is None:
                    try:
                        await self.adapter.report_source_failure(
                            SourceFailure(provider=provider, source_ref=url, fetched_at_utc=now)
                        )
                    except Exception:
                        pass  # database outage cannot prevent independent provider progress
                return False
            state.pending = None
            state.failures = 0
            state.due = now + timedelta(seconds=interval)
            LOGGER.info("%s live batch committed", provider)
            return True

    async def run_provider(self, provider: str) -> None:
        while True:
            await self.poll(provider)
            state = self.states[provider]
            delay = max(0.1, (state.due - self.clock()).total_seconds()) if state.due else 1
            await asyncio.sleep(delay)

    async def run(self) -> None:
        await asyncio.gather(*(self.run_provider(provider) for provider in self.states))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Poll live ADS-B/FAA into WatchTower's database")
    parser.add_argument("--env-file", type=Path, help="Explicit backend .env; process values win")
    parser.add_argument(
        "--provider", action="append", choices=("adsb_lol", "faa"), help="Default: both providers"
    )
    parser.add_argument("--user-agent", help="Application identifier for public feed requests")
    args = parser.parse_args(argv)
    try:
        environment = read_environment(args.env_file, os.environ)
        if environment.get("APP_MODE", "live") != "live":
            raise ValueError("Live ingestion requires live mode")
        if args.user_agent is not None:
            environment["DATA_USER_AGENT"] = args.user_agent
        service = IngestionService(
            SpacetimeAdapter(SpacetimeSettings.from_env(environment)),
            IngestionConfig.from_env(environment),
        )
    except (OSError, ValueError):
        parser.error(
            "Unable to load valid live ingestion settings. Check --env-file, database origin, "
            "ingestion token and DATA_USER_AGENT; Python 3.12/3.13 is required."
        )
    providers = tuple(dict.fromkeys(args.provider or ("adsb_lol", "faa")))
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    LOGGER.info("Starting live ingestion: %s", ", ".join(providers))

    async def run_selected() -> None:
        await asyncio.gather(*(service.run_provider(provider) for provider in providers))

    try:
        asyncio.run(run_selected())
    except KeyboardInterrupt:
        LOGGER.info("Live ingestion stopped")


if __name__ == "__main__":
    main()
