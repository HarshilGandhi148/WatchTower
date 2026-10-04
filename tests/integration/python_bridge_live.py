"""Real SDK -> authenticated Python -> per-role HTTP writes (isolated TEST DB)."""

import asyncio
import json
import os
import sqlite3
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from uuid import uuid4

from agents.congestion.stream import handle as congestion
from agents.validation.stream import handle as validation
from agents.weather.stream import handle as weather_availability_test
from runtime.bridge_listener import Dispatcher, server
from shared.settings import SpacetimeSettings
from shared.spacetimedb_adapter import SpacetimeError, http_transport
from watchtower_client import Client


async def exercise_bridge(origin: str, temp: Path) -> None:
    from tests.integration.spacetimedb_live import ROOT, deny, request

    database = f"watchtower-test-bridge-{uuid4().hex}"
    identities = {
        role: json.loads(request(f"{origin}/v1/identity"))
        for role in (
            "admin",
            "ingestion",
            "weather",
            "congestion",
            "validation",
            "bridge",
            "outsider",
        )
    }
    result = json.loads(
        request(
            f"{origin}/v1/database/{database}?host_type=Js",
            (ROOT / "backend/module/dist/bundle.js").read_bytes(),
            identities["admin"]["token"],
            "PUT",
        )
    )
    assert "Success" in result
    clients = {
        role: Client(SpacetimeSettings(origin, database, value["token"]))
        for role, value in identities.items()
    }
    for role in ("ingestion", "weather", "congestion", "validation", "bridge"):
        await clients["admin"].call("seed_agent_identity", role, identities[role]["identity"])
    # The separate stream allowlist must not override a revoked service permission.
    await clients["admin"].call(
        "set_ingestion_permission", identities["ingestion"]["identity"], False
    )
    deny(
        lambda: request(
            f"{origin}/v1/database/{database}/call/write_ingest_event",
            json.dumps(["TEST-revoked", 0, '{"schema_version":1}']).encode(),
            identities["ingestion"]["token"],
        )
    )
    await clients["admin"].call(
        "seed_agent_identity", "ingestion", identities["ingestion"]["identity"]
    )
    # Every service token is denied on every other stream reducer, including bridge.
    reducers = {
        "ingestion": "write_ingest_event",
        "weather": "write_weather_reading",
        "congestion": "write_congestion_reading",
        "validation": "write_validation_result",
    }
    for caller, identity in identities.items():
        for writer, reducer in reducers.items():
            if caller != writer:
                deny(
                    lambda identity=identity, reducer=reducer: request(
                        f"{origin}/v1/database/{database}/call/{reducer}",
                        json.dumps(["unauthorized", 0, '{"schema_version":1}']).encode(),
                        identity["token"],
                    )
                )
    deny(
        lambda: request(
            f"{origin}/v1/database/{database}/call/seed_agent_identity",
            json.dumps(["bridge", identities["outsider"]["identity"]]).encode(),
            identities["bridge"]["token"],
        )
    )
    deny(
        lambda: request(
            f"{origin}/v1/database/{database}/call/seed_agent_identity",
            json.dumps(["weather", identities["bridge"]["identity"]]).encode(),
            identities["admin"]["token"],
        )
    )

    dispatcher = Dispatcher(str(temp / "receipts.sqlite3"))
    completed = Event()
    expected_results = 1
    counts = {"weather": 0, "congestion": 0, "validation": 0}

    fail_first_delivery = True

    async def specialist(event, role, handler):
        nonlocal fail_first_delivery
        if fail_first_delivery:
            fail_first_delivery = False
            raise RuntimeError("Controlled TEST webhook failure")
        counts[role] += 1
        await handler(event, clients[role])

    async def verdict(event):
        counts["validation"] += 1
        await validation(event, clients["validation"])
        if len(await clients["bridge"].sql("SELECT * FROM validation_result")) >= expected_results:
            completed.set()

    dispatcher.register(
        "ingest_event", "weather", lambda e: specialist(e, "weather", weather_availability_test)
    )
    dispatcher.register(
        "ingest_event", "congestion", lambda e: specialist(e, "congestion", congestion)
    )
    for table in ("ingest_event", "weather_reading", "congestion_reading"):
        dispatcher.register(table, "validation", verdict)
    secret = "TEST-only-webhook-secret-32-characters"
    listener = server(dispatcher, clients["bridge"], secret, port=0)
    thread = Thread(target=listener.serve_forever, daemon=True)
    thread.start()
    webhook = f"http://127.0.0.1:{listener.server_port}/events"
    env = {
        **os.environ,
        "SPACETIMEDB_HTTP_URL": origin,
        "SPACETIMEDB_WS_URL": origin.replace("http:", "ws:"),
        "SPACETIMEDB_DATABASE": database,
        "SPACETIMEDB_BRIDGE_TOKEN": identities["bridge"]["token"],
        "BRIDGE_WEBHOOK_URL": webhook,
        "BRIDGE_WEBHOOK_SECRET": secret,
        "BRIDGE_CURSOR_PATH": str(temp / "cursors.json"),
    }

    def start():
        return subprocess.Popen(
            ["node", str(temp / "bridge.cjs")],
            env=env,
            cwd=ROOT,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def stop(process):
        process.terminate()
        process.wait(timeout=10)

    now = datetime.now(UTC).isoformat()
    payload = {
        "schema_version": 1,
        "data_mode": "TEST",
        "weather": {"observed_at_utc": now, "wind_speed_kt": 10},
        "traffic": {"fetch_succeeded": True, "fetched_at_utc": now, "observations": []},
    }
    bridge = start()
    try:
        await clients["ingestion"].write("ingest_event", "TEST-input", 0, payload)
        assert await asyncio.to_thread(completed.wait, 15), "SDK bridge pipeline timeout"
        rows = await clients["bridge"].sql("SELECT * FROM validation_result")
        assert len(rows) == 1
        assert json.loads(rows[0]["payload_json"])["outcome"] == "insufficient_data"
        assert counts["weather"] == counts["congestion"] == 1
        # Lost commit response: same key retry must produce one ingestion row.
        lost = True

        def lost_response(url, body, headers, timeout):
            nonlocal lost
            response = http_transport(url, body, headers, timeout)
            if lost:
                lost = False
                raise SpacetimeError("connection or timeout")
            return response

        retry = Client(clients["ingestion"].settings, transport=lost_response, backoff=0)
        await retry.write("ingest_event", "TEST-input", 0, payload)
        assert len(await clients["bridge"].sql("SELECT * FROM ingest_event")) == 1
        try:
            await clients["ingestion"].write(
                "ingest_event", "TEST-input", 0, {"schema_version": 1, "changed": True}
            )
        except SpacetimeError:
            pass
        else:
            raise AssertionError("Conflicting key accepted")
        # Duplicate webhook re-fetches persisted row and skips completed handlers.
        await asyncio.to_thread(request, webhook, b'{"table":"ingest_event","row_id":"1"}', secret)
        assert counts["weather"] == counts["congestion"] == 1
        stop(bridge)
        bridge = None
        # Receiver also restarts with the persisted handler receipts.
        dispatcher.db.close()
        dispatcher.db = sqlite3.connect(temp / "receipts.sqlite3", check_same_thread=False)
        expected_results = 2
        completed.clear()
        await clients["ingestion"].write("ingest_event", "TEST-offline-input", 0, payload)
        bridge = start()
        assert await asyncio.to_thread(completed.wait, 15), "Restart recovery timeout"
        assert counts["weather"] == counts["congestion"] == 2
        assert len(await clients["bridge"].sql("SELECT * FROM validation_result")) == 2
        # Authenticated stream lookup prevents forged bodies from replacing persisted data.
        deny(lambda: request(webhook, b'{"table":"ingest_event","row_id":"1"}', "wrong-token"))
        deny(lambda: request(webhook, b'{"table":"unknown","row_id":"1"}', secret))
    finally:
        if bridge is not None:
            stop(bridge)
        listener.shutdown()
        listener.server_close()
        thread.join(timeout=5)
        dispatcher.db.close()
