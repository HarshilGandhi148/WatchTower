"""Explicit real-server acceptance test: python -m tests.integration.spacetimedb_live.

Never skipped when tooling is missing. Creates its own local server, keys and
unique TEST database; no .env or existing CLI credentials are read.
"""

import asyncio
import json
import shutil
import socket
import subprocess
import tempfile
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from queue import Queue
from threading import Thread
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

from shared.contracts import AircraftBatch, SourceFailure
from shared.settings import SpacetimeSettings
from shared.spacetimedb_adapter import SpacetimeAdapter, SpacetimeError, payload_json
from tests.fixtures.spacetimedb import aircraft, weather
from tests.integration.congestion_live import exercise_congestion

ROOT = Path(__file__).resolve().parents[2]


def request(url: str, data: bytes = b"", token: str | None = None, method: str = "POST") -> bytes:
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        with urlopen(
            Request(url, data=data, method=method, headers=headers), timeout=20
        ) as response:
            return response.read()
    except HTTPError as error:
        status = error.code
        error.close()
        raise AssertionError(f"TEST HTTP request rejected ({status})") from None


def run_tool(args: list[str]) -> None:
    result = subprocess.run(args, cwd=ROOT, capture_output=True, timeout=120)
    if result.returncode:
        # Build/codegen output contains no identities/tokens. Print only on failure.
        raise AssertionError(f"Tool failed: {result.stderr.decode()[-2000:]}")


def deny(call) -> None:
    try:
        call()
    except (SpacetimeError, AssertionError):
        return
    raise AssertionError("Unauthorized/invalid request unexpectedly succeeded")


async def exercise(origin: str, database: str, temp: Path) -> None:
    admin, ingest, viewer = [json.loads(request(f"{origin}/v1/identity")) for _ in range(3)]
    published = json.loads(
        request(
            f"{origin}/v1/database/{database}?host_type=Js",
            (ROOT / "backend/module/dist/bundle.js").read_bytes(),
            admin["token"],
            "PUT",
        )
    )
    assert "Success" in published, "Module publish failed"

    def reducer(name: str, args: list[object], token: str | None = None) -> bytes:
        return request(
            f"{origin}/v1/database/{database}/call/{name}", json.dumps(args).encode(), token
        )

    reducer("set_ingestion_permission", [ingest["identity"], True], admin["token"])
    adapter = SpacetimeAdapter(SpacetimeSettings(origin, database, ingest["token"]))
    viewer_adapter = SpacetimeAdapter(SpacetimeSettings(origin, database, viewer["token"]))
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=20)
    record = weather(at)
    for token in (None, viewer["token"], admin["token"]):
        deny(lambda token=token: reducer("ingest_weather_record", [payload_json(record)], token))
    deny(lambda: reducer("set_ingestion_permission", [viewer["identity"], True], ingest["token"]))
    deny(lambda: reducer("set_ingestion_permission", [admin["identity"], False], admin["token"]))
    assert await adapter.read_observations("weather_records") == []
    deny(
        lambda: request(
            f"{origin}/v1/database/{database}/sql",
            b"SELECT * FROM service_permissions",
            viewer["token"],
        )
    )

    events: Queue[dict] = Queue()
    clients = subprocess.Popen(
        ["node", str(temp / "clients.cjs"), origin.replace("http:", "ws:"), database],
        cwd=ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )

    def reader() -> None:
        assert clients.stdout
        for line in clients.stdout:
            if line.startswith(b"TEST_EVENT "):
                events.put(json.loads(line[len(b"TEST_EVENT ") :]))

    Thread(target=reader, daemon=True).start()

    def both(event: str, **expected: object) -> None:
        seen: set[int] = set()
        deadline = time.monotonic() + 20
        while len(seen) < 2:
            message = events.get(timeout=max(0.01, deadline - time.monotonic()))
            assert message.get("event") != "error", "SDK client failed"
            if message.get("event") == event and all(
                message.get(key) == value for key, value in expected.items()
            ):
                seen.add(message["client"])
            assert time.monotonic() < deadline, "Both subscriptions must receive the update"

    try:
        both("ready")
        await adapter.ingest_weather_record(record)
        both("weather", speed=10)
        rows = await adapter.read_observations("weather_records")
        assert rows[0]["wind_speed_kt"] == 10, "HTTP decoding must preserve typed columns"
        assert rows[0]["visibility_m"] is None
        assert rows[0]["observed_at_utc"] == at
        before = rows
        await adapter.ingest_weather_record(record)
        assert await adapter.read_observations("weather_records") == before
        newer = weather(at + timedelta(seconds=2), 12)
        await adapter.ingest_weather_record(newer)
        both("weather", speed=12)
        await adapter.ingest_weather_record(record)
        assert (await adapter.read_observations("weather_records"))[0]["wind_speed_kt"] == 12
        conflict = weather(at + timedelta(seconds=2), 99)
        deny(lambda: reducer("ingest_weather_record", [payload_json(conflict)], ingest["token"]))
        for change in (
            {"data_mode": "test"},
            {"data_mode": "projection"},
            {"wind_speed_kt": -1},
            {"scenario_parameters": {}},
        ):
            raw = json.loads(payload_json(newer)) | change
            deny(
                lambda raw=raw: reducer("ingest_weather_record", [json.dumps(raw)], ingest["token"])
            )

        batch = aircraft(at)
        await adapter.ingest_aircraft_batch(batch)
        both("aircraft", id="test-plane")
        await adapter.ingest_aircraft_batch(batch)
        positions = await adapter.read_observations("aircraft_observations")
        assert len(positions) == 1
        await adapter.ingest_aircraft_batch(
            AircraftBatch(
                fetched_at_utc=at + timedelta(seconds=1),
                source_ref=batch.source_ref,
                observations=(),
            )
        )
        assert await adapter.read_observations("aircraft_observations") == positions
        older_batch = replace(
            aircraft(at - timedelta(seconds=1)), fetched_at_utc=at + timedelta(seconds=2)
        )
        await adapter.ingest_aircraft_batch(older_batch)
        assert await adapter.read_observations("aircraft_observations") == positions
        aircraft_health = {
            row["provider"]: row for row in await adapter.read_observations("source_health")
        }
        assert aircraft_health["adsb_lol"]["source_at_utc"] == at
        raw = json.loads(payload_json(batch))
        # First row would insert, second conflicts: whole reducer must roll back.
        raw["observations"] = [
            raw["observations"][0] | {"aircraft_id": "rollback-plane"},
            raw["observations"][0] | {"speed_kt": 50},
        ]
        deny(lambda: reducer("ingest_aircraft_batch", [json.dumps(raw)], ingest["token"]))
        assert await adapter.read_observations("aircraft_observations") == positions
        raw["observations"][1]["latitude_deg"] = 999
        deny(lambda: reducer("ingest_aircraft_batch", [json.dumps(raw)], ingest["token"]))
        assert await adapter.read_observations("aircraft_observations") == positions

        failure = SourceFailure(
            provider="awc", source_ref=record.source_ref, fetched_at_utc=at + timedelta(seconds=3)
        )
        await adapter.report_source_failure(failure)
        await adapter.report_source_failure(failure)
        health = {row["provider"]: row for row in await adapter.read_observations("source_health")}
        assert health["awc"]["status"] == "failed" and health["awc"]["failure_count"] == 1
        assert health["awc"]["last_success_at_utc"] == newer.fetched_at_utc
        assert (await adapter.read_observations("weather_records"))[0]["wind_speed_kt"] == 12
        # Conflicting equal-time health outcome must roll back the observation too.
        deny(
            lambda: reducer(
                "ingest_weather_record",
                [payload_json(weather(failure.fetched_at_utc, 15))],
                ingest["token"],
            )
        )
        assert (await adapter.read_observations("weather_records"))[0]["wind_speed_kt"] == 12

        await asyncio.gather(
            adapter.ingest_weather_record(weather(at + timedelta(seconds=5), 20)),
            adapter.ingest_weather_record(weather(at + timedelta(seconds=4), 18)),
        )
        both("weather", speed=20)
        assert (await adapter.read_observations("weather_records"))[0]["wind_speed_kt"] == 20
        assert len(await viewer_adapter.read_observations("weather_records")) == 1
        reducer("set_ingestion_permission", [ingest["identity"], False], admin["token"])
        deny(
            lambda: reducer(
                "ingest_weather_record",
                [payload_json(weather(at + timedelta(seconds=6)))],
                ingest["token"],
            )
        )
        await exercise_congestion(origin, database, admin, ingest, request, reducer)
    finally:
        if clients.stdin:
            clients.stdin.close()
        try:
            clients.wait(timeout=5)
        except subprocess.TimeoutExpired:
            clients.terminate()
            clients.wait(timeout=5)


def main() -> None:
    cli = shutil.which("spacetime")
    assert cli, "SpacetimeDB CLI 2.10.2 is required; test cannot be skipped"
    version = subprocess.check_output([cli, "--version"], text=True)
    assert "tool version 2.10.2;" in version, "SpacetimeDB CLI 2.10.2 is required"
    run_tool([cli, "build", "--module-path", "backend/module"])
    with tempfile.TemporaryDirectory(prefix="watchtower-db-test-") as directory:
        temp = Path(directory)
        generated = temp / "bindings"
        run_tool(
            [
                cli,
                "generate",
                "--lang",
                "typescript",
                "--module-path",
                "backend/module",
                "--out-dir",
                str(generated),
                "--no-config",
                "--yes",
            ]
        )
        committed = ROOT / "frontend/src/module_bindings"
        assert {p.relative_to(generated) for p in generated.rglob("*.ts")} == {
            p.relative_to(committed) for p in committed.rglob("*.ts")
        }, "Generated binding file set differs"
        for path in generated.rglob("*.ts"):
            assert path.read_bytes() == (committed / path.relative_to(generated)).read_bytes(), (
                "Generated bindings differ from schema"
            )
        run_tool(
            [
                str(ROOT / "backend/module/node_modules/.bin/esbuild"),
                "tests/integration/spacetime_clients.ts",
                "--bundle",
                "--platform=node",
                "--format=cjs",
                f"--outfile={temp / 'clients.cjs'}",
            ]
        )
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        origin = f"http://127.0.0.1:{port}"
        with (temp / "server.log").open("wb") as log:
            server = subprocess.Popen(
                [
                    cli,
                    f"--config-path={temp / 'cli.toml'}",
                    "start",
                    "--listen-addr",
                    f"127.0.0.1:{port}",
                    "--data-dir",
                    str(temp / "database"),
                    "--in-memory",
                    "--non-interactive",
                ],
                cwd=ROOT,
                stdout=log,
                stderr=log,
            )
            try:
                deadline = time.monotonic() + 20
                while True:
                    assert server.poll() is None, (
                        "TEST server failed to start: " + (temp / "server.log").read_text()[-2000:]
                    )
                    try:
                        with urlopen(f"{origin}/v1/identity/public-key", timeout=1):
                            break
                    except (OSError, URLError):
                        assert time.monotonic() < deadline, "TEST server startup timed out"
                        time.sleep(0.1)
                asyncio.run(exercise(origin, f"watchtower-test-{uuid4().hex}", temp))
            finally:
                server.terminate()
                try:
                    server.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    server.kill()
                    server.wait(timeout=5)
    print(
        "PASS: real SpacetimeDB publish, Python writes/reads, two subscriptions, "
        "authorization, rollback, snapshots, durable candidates, correction, receipt, recovery, "
        "projection isolation and concurrent updates (TEST database; TEST weather double)."
    )


if __name__ == "__main__":
    main()
