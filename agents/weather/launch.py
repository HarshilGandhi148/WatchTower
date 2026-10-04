"""Local read-only Weather Agent server and inspector; no operational case store."""

from __future__ import annotations

import argparse
import asyncio
import os
import secrets
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

import uagents.asgi
from uagents import Agent
from uagents_core.identity import Identity
from uagents_core.registration import AgentRegistrationPolicy
from uagents_core.types import AgentEndpoint

from agents.weather.chat import WeatherChat, attach_chat
from agents.weather.llm import AsiConfig, AsiModel, Explainer
from data.awc import AwcClient, AwcError, FetchResult, Product
from shared.contracts import MetarEvidence, TafEvidence, WeatherSnapshot

ENV_NAMES = frozenset(
    {
        "WEATHER_AGENT_SEED",
        "ASI_ONE_API_KEY",
        "ASI_ONE_BASE_URL",
        "ASI_ONE_MODEL",
        "AWC_BASE_URL",
        "DATA_USER_AGENT",
        "APP_MODE",
        "AIRPORT_ICAO",
    }
)


def read_settings(path: Path | None, environ: Mapping[str, str]) -> dict[str, str]:
    """Read only explicit backend settings; no shell expansion or secret diagnostics."""
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


class LocalRegistration(AgentRegistrationPolicy):
    """Local inspection does not require public Almanac registration."""

    async def register(
        self,
        agent_identifier: str,
        identity: Identity,
        protocols: list[str],
        endpoints: list[AgentEndpoint],
        metadata: dict[str, Any] | None = None,
    ) -> None:
        pass


class LiveWeatherReader:
    """On-demand AWC reads for chat; ephemeral evidence, never database ingestion."""

    def __init__(
        self, client: AwcClient, *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    ) -> None:
        self._client, self._clock = client, clock
        self._results: dict[Product, FetchResult] = {}
        self._next: dict[Product, datetime] = {}
        self._lock = asyncio.Lock()

    async def _refresh(self, product: Product) -> None:
        now = self._clock()
        if product in self._next and now < self._next[product]:
            return
        interval = 60.0 if product == "metar" else 300.0
        try:
            self._results[product] = await self._client.fetch(product)
        except AwcError as error:
            # Failed retrieval remains missing; never synthesize clear conditions.
            self._results.pop(product, None)
            interval = max(interval, error.retry_after_s or 0)
        try:
            self._next[product] = self._clock() + timedelta(seconds=interval)
        except OverflowError:
            self._next[product] = datetime.max.replace(tzinfo=UTC)

    async def snapshot(self) -> WeatherSnapshot:
        async with self._lock:
            await asyncio.gather(self._refresh("metar"), self._refresh("taf"))
            records = tuple(r for result in self._results.values() for r in result.records)
            return WeatherSnapshot(
                snapshot_id="weather-chat-" + str(uuid4()),
                cutoff_utc=self._clock(),
                evidence_ids=tuple(r.evidence_id for r in records),
                metars=tuple(r for r in records if isinstance(r, MetarEvidence)),
                tafs=tuple(r for r in records if isinstance(r, TafEvidence)),
            )


def build_inspector_agent(
    settings: Mapping[str, str], *, port: int = 8001, mailbox: bool = False
) -> Agent:
    if not 1 <= port <= 65535:
        raise ValueError("Port must be between 1 and 65535")
    if settings.get("APP_MODE", "live") != "live" or settings.get("AIRPORT_ICAO", "KDTW") != "KDTW":
        raise ValueError("Inspector launcher supports live KDTW weather only")
    seed = settings.get("WEATHER_AGENT_SEED", "").strip() or secrets.token_urlsafe(48)
    config = AsiConfig.from_env(settings)
    reader = LiveWeatherReader(
        AwcClient(
            settings.get("DATA_USER_AGENT", "WatchTowerWeather/0.1 (KDTW local inspector)"),
            base_url=settings.get("AWC_BASE_URL", "https://aviationweather.gov/api/data"),
        )
    )
    agent = Agent(
        name="weather",
        seed=seed,
        port=port,
        endpoint=f"http://127.0.0.1:{port}/submit",
        mailbox=mailbox,
        enable_agent_inspector=True,
        publish_agent_details=False,
        store_message_history=False,
        report_events=False,
        mark_inactive_on_shutdown=mailbox,
        registration_policy=None if mailbox else LocalRegistration(),
    )
    attach_chat(
        agent,
        WeatherChat(reader.snapshot, explainer=Explainer(AsiModel(config) if config else None)),
    )
    return agent


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Launch the read-only KDTW weather inspector")
    parser.add_argument("--port", type=int, default=8001)
    parser.add_argument(
        "--env-file", type=Path, help="Explicit local .env; process settings take precedence"
    )
    parser.add_argument(
        "--mailbox", action="store_true", help="Enable Agentverse mailbox connection"
    )
    args = parser.parse_args(argv)
    try:
        settings = read_settings(args.env_file, os.environ)
        # uAgents 0.26 exposes its listener host as this module constant.
        # Keep unauthenticated inspector endpoints on loopback in this standalone process.
        uagents.asgi.HOST = "127.0.0.1"
        agent = build_inspector_agent(settings, port=args.port, mailbox=args.mailbox)
    except (OSError, ValueError, UnicodeError):
        parser.error(
            "Cannot configure weather launcher. Check the port and backend environment settings."
        )
    if not settings.get("WEATHER_AGENT_SEED", "").strip():
        print("Temporary identity: set WEATHER_AGENT_SEED to keep the same address after restart.")
    print("Read-only weather chat; operational case handling is not connected.")
    print("Inspector keeps recent messages in memory; disk message history is disabled.")
    # SDK 0.26's synchronous runner has no annotation; its signature is run() -> None.
    run: Callable[[], None] = agent.run
    run()


if __name__ == "__main__":
    main()
