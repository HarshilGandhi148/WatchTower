"""Run the three real specialists against the configured live WatchTower database."""

import argparse
import asyncio
import logging
import os
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path

from uagents import Bureau
from uagents_core.identity import Identity

from agents.congestion.agent import AgentConfig as CongestionConfig
from agents.congestion.agent import build_agent as build_congestion
from agents.congestion.persistence import PersistentCongestionService
from agents.congestion.service import CongestionService
from agents.validation.agent import AgentConfig as ValidationConfig
from agents.validation.agent import build_agent as build_validation
from agents.weather.agent import AgentConfig as WeatherConfig
from agents.weather.agent import build_agent as build_weather
from agents.weather.persistence import WeatherStorage
from agents.weather.service import WeatherDependencies
from data.awc import AwcClient
from data.weather_ingestion import WeatherIngestion
from runtime.bureau import build_bureau
from shared.contracts import AgentName
from shared.settings import SpacetimeSettings
from shared.spacetimedb_adapter import SpacetimeAdapter, SpacetimeError

LOGGER = logging.getLogger("watchtower.runtime")
ENV_NAMES = frozenset(
    {
        "APP_MODE",
        "AIRPORT_ICAO",
        "AIRPORT_LATITUDE_DEG",
        "AIRPORT_LONGITUDE_DEG",
        "TRAFFIC_RADIUS_NM",
        "AWC_BASE_URL",
        "DATA_USER_AGENT",
        "SPACETIMEDB_HTTP_URL",
        "SPACETIMEDB_DATABASE",
        "SPACETIMEDB_INGEST_TOKEN",
        "SPACETIMEDB_WEATHER_TOKEN",
        "SPACETIMEDB_CONGESTION_TOKEN",
        "SPACETIMEDB_VALIDATION_TOKEN",
        "WEATHER_AGENT_SEED",
        "CONGESTION_AGENT_SEED",
        "VALIDATION_AGENT_SEED",
        "WEATHER_AGENT_ADDRESS",
        "CONGESTION_AGENT_ADDRESS",
        "VALIDATION_AGENT_ADDRESS",
        "ASI_ONE_API_KEY",
        "ASI_ONE_BASE_URL",
        "ASI_ONE_MODEL",
        "DEPARTURE_CAPACITY_PER_HOUR",
        "DEPARTURE_BACKLOG_UPSTREAM",
        "DEPARTURE_BACKLOG_RUNWAY_QUEUE",
    }
)


def read_settings(path: Path | None, environ: Mapping[str, str]) -> dict[str, str]:
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
                raise ValueError("Expected NAME=value settings")
            name, value = name.strip(), value.strip()
            if name in ENV_NAMES:
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                settings[name] = value
    settings.update({name: value for name, value in environ.items() if name in ENV_NAMES})
    return settings


def configured_peers(environment: Mapping[str, str]) -> dict[str, str]:
    """Derive peer addresses from the three fixed seeds, without creating extra agents."""
    env = dict(environment)
    if env.get("APP_MODE", "live") != "live":
        raise ValueError("Live runtime requires APP_MODE=live")
    addresses = []
    for role in AgentName:
        prefix = role.value.upper() + "_AGENT_"
        seed = env.get(prefix + "SEED", "").strip()
        if not seed:
            raise ValueError("Missing agent seed")
        address = Identity.from_seed(seed, 0).address
        existing = env.get(prefix + "ADDRESS", "").strip()
        if existing and existing != address:
            raise ValueError("Agent address does not match its seed")
        env[prefix + "ADDRESS"] = address
        addresses.append(address)
    if len(set(addresses)) != 3:
        raise ValueError("Three distinct agent seeds are required")
    return env


async def prepare_runtime(
    environment: Mapping[str, str], *, port: int = 8000
) -> tuple[Bureau, WeatherIngestion]:
    env = configured_peers(environment)
    # Validate all credentials/config before preparing any agent or writing observations.
    weather_config = WeatherConfig.from_env(env)
    congestion_config = CongestionConfig.from_env(env)
    validation_config = replace(ValidationConfig.from_env(env), mailbox=False)
    ingest = SpacetimeAdapter(SpacetimeSettings.from_env(env))
    weather_store = WeatherStorage(
        SpacetimeAdapter(SpacetimeSettings.from_env(env, role="weather"))
    )
    client = AwcClient(
        env.get("DATA_USER_AGENT", ""),
        base_url=env.get("AWC_BASE_URL", "https://aviationweather.gov/api/data"),
    )
    loop = asyncio.get_running_loop()
    congestion_service = PersistentCongestionService(
        SpacetimeAdapter(congestion_config.database),
        configuration=congestion_config.airport,
        service=CongestionService(projection=congestion_config.projection),
    )
    # One recovery finishes before any specialist opens a snapshot. Two concurrent
    # startup recoveries could otherwise close the first newly opened live case.
    await congestion_service.initialize()
    agents = {
        AgentName.WEATHER: build_weather(
            weather_config,
            WeatherDependencies(weather_store.load_snapshot, weather_store.persist_assessment),
            loop=loop,
        ),
        AgentName.CONGESTION: build_congestion(
            congestion_config, service=congestion_service, loop=loop
        ),
        AgentName.VALIDATION: build_validation(
            validation_config, loop=loop, recover_on_start=False
        ),
    }
    return (
        build_bureau(
            agents, port=port, endpoint=f"http://127.0.0.1:{port}/submit", local_only=True
        ),
        WeatherIngestion(client, ingest),
    )


async def run(environment: Mapping[str, str], *, port: int = 8000) -> None:
    bureau, weather = await prepare_runtime(environment, port=port)
    await weather.poll()
    polling = asyncio.create_task(weather.run())
    LOGGER.info("Starting weather, congestion and validation; live cases refresh every minute")
    try:
        await bureau.run_async()  # type: ignore[no-untyped-call]
    finally:
        polling.cancel()
        await asyncio.gather(polling, return_exceptions=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--user-agent")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument(
        "--derive-peer-addresses",
        action="store_true",
        help="Derive local peers from seeds; override saved addresses only in memory",
    )
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    try:
        settings = read_settings(args.env_file, os.environ)
        if args.user_agent:
            settings["DATA_USER_AGENT"] = args.user_agent
        if args.derive_peer_addresses:
            for role in AgentName:
                settings.pop(role.value.upper() + "_AGENT_ADDRESS", None)
            LOGGER.info("Using local peer addresses derived from the three configured seeds")
        asyncio.run(run(settings, port=args.port))
    except (ValueError, OSError, SpacetimeError):
        raise SystemExit(
            "Live agents not started: check backend settings and the runtime port"
        ) from None
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
