"""Independent public ADS-B/FAA polling into SpacetimeDB (infrastructure).

Paid data polling is deliberately absent. Network and persistence errors are redacted;
each source has its own deadline/backoff and successful empty batches are retained.
"""

import asyncio
import json
import os
import random
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from http.client import HTTPMessage
from urllib.error import HTTPError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid4

from data.adsb import parse_point_response, point_url
from data.faa_status import parse_status_xml
from shared.contracts import SourceFailure
from shared.settings import AirportConfig, SpacetimeSettings
from shared.spacetimedb_adapter import SpacetimeAdapter

MAX_BODY = 2_000_000
TIMEOUT_S = 10
Get = Callable[[str, str], bytes]


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
            return True

    async def run_provider(self, provider: str) -> None:
        while True:
            await self.poll(provider)
            state = self.states[provider]
            delay = max(0.1, (state.due - self.clock()).total_seconds()) if state.due else 1
            await asyncio.sleep(delay)

    async def run(self) -> None:
        await asyncio.gather(*(self.run_provider(provider) for provider in self.states))


if __name__ == "__main__":
    asyncio.run(
        IngestionService(
            SpacetimeAdapter(SpacetimeSettings.from_env()), IngestionConfig.from_env(os.environ)
        ).run()
    )
