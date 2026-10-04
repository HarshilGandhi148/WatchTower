"""Authenticated local webhook dispatcher with durable per-handler receipts."""

import asyncio
import hmac
import json
import logging
import os
import sqlite3
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from typing import Any

from shared.bridge import STREAMS, StreamEvent
from watchtower_client import Client
from watchtower_client.client import configure_logging

type Handler = Callable[[StreamEvent], Awaitable[None]]


class Dispatcher:
    def __init__(self, path: str) -> None:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS receipts (table_name TEXT, row_id TEXT, "
            "handler TEXT, PRIMARY KEY(table_name, row_id, handler))"
        )
        self.db.commit()
        self.handlers: dict[str, dict[str, Handler]] = {}
        self.lock = Lock()
        self.async_lock = asyncio.Lock()

    def register(self, table: str, name: str, handler: Handler) -> None:
        if table not in STREAMS:
            raise ValueError("Unknown stream")
        if name in self.handlers.get(table, {}):
            raise ValueError("Duplicate handler")
        self.handlers.setdefault(table, {})[name] = handler

    async def dispatch(self, event: StreamEvent) -> None:
        async with self.async_lock:
            await self._dispatch(event)

    async def _dispatch(self, event: StreamEvent) -> None:
        for name, handler in self.handlers.get(event.table, {}).items():
            key = (event.table, str(event.row_id), name)
            if self.db.execute(
                "SELECT 1 FROM receipts WHERE table_name=? AND row_id=? AND handler=?", key
            ).fetchone():
                continue
            await handler(event)
            self.db.execute("INSERT INTO receipts VALUES (?, ?, ?)", key)
            self.db.commit()


def server(
    dispatcher: Dispatcher, reader: Client, secret: str, port: int = 8090
) -> ThreadingHTTPServer:
    if len(secret) < 32:
        raise ValueError("Bridge webhook secret must contain at least 32 characters")

    class Receiver(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass  # No URLs, bodies, headers or credentials in access logs.

        def do_POST(self) -> None:
            if self.path != "/events":
                self.send_error(404)
                return
            if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {secret}"):
                self.send_error(401)
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if not 0 < size <= 8192:
                    raise ValueError
                value: Any = json.loads(self.rfile.read(size))
                table, row_id = value["table"], value["row_id"]
                if table not in STREAMS or not isinstance(row_id, str) or not row_id.isdecimal():
                    raise ValueError
                identifier = int(row_id)
                if not 0 < identifier < 2**64:
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                self.send_error(400)
                return

            async def receive() -> None:
                rows = await reader.sql(f"SELECT * FROM {table} WHERE id = {identifier}")
                if len(rows) != 1:
                    raise ValueError("Missing persisted stream row")
                row = rows[0]
                at = datetime.fromtimestamp(row["created_at_utc"][0] / 1_000_000, UTC).isoformat()
                event = StreamEvent(
                    table, identifier, int(row["root_id"]), at, json.loads(row["payload_json"])
                )
                await dispatcher.dispatch(event)

            try:
                # Serialize decisions across request threads. Receipts commit after each handler.
                with dispatcher.lock:
                    asyncio.run(receive())
            except Exception:
                logging.getLogger("watchtower").warning(
                    "Bridge handler failed; delivery will retry"
                )
                self.send_error(503)
                return
            self.send_response(204)
            self.end_headers()

    return ThreadingHTTPServer(("127.0.0.1", port), Receiver)


def main() -> None:
    # Composition point only: specialists never import another agent's implementation.
    from agents.congestion.stream import handle as congestion
    from agents.validation.stream import handle as validation
    from agents.weather.stream import handle as weather

    configure_logging()
    dispatcher = Dispatcher(
        os.environ.get("BRIDGE_RECEIPTS_PATH", ".cache/bridge-receipts.sqlite3")
    )
    weather_client, congestion_client, validation_client = (
        Client.from_env(role) for role in ("weather", "congestion", "validation")
    )
    dispatcher.register("ingest_event", "weather", lambda e: weather(e, weather_client))
    dispatcher.register("ingest_event", "congestion", lambda e: congestion(e, congestion_client))
    for table in ("ingest_event", "weather_reading", "congestion_reading"):
        dispatcher.register(table, "validation", lambda e: validation(e, validation_client))
    listener = server(dispatcher, Client.from_env("bridge"), os.environ["BRIDGE_WEBHOOK_SECRET"])
    try:
        listener.serve_forever()
    finally:
        listener.server_close()
        dispatcher.db.close()


if __name__ == "__main__":
    main()
