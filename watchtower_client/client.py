"""Bounded, redacted HTTP calls with retries for stable-key writes and SQL reads."""

import asyncio
import json
import logging
import re
from collections.abc import Callable
from typing import Any

from shared.settings import SpacetimeSettings
from shared.spacetimedb_adapter import SpacetimeError, Transport, http_transport

logger = logging.getLogger("watchtower")


def configure_logging() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class Client:
    def __init__(
        self,
        settings: SpacetimeSettings,
        *,
        transport: Transport = http_transport,
        attempts: int = 4,
        backoff: float = 0.2,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        if not 1 <= attempts <= 8 or not 0 <= backoff <= 10:
            raise ValueError("Invalid retry bounds")
        self.settings = settings
        self.transport = transport
        self.attempts = attempts
        self.backoff = backoff
        self.sleep = sleep

    @classmethod
    def from_env(cls, role: str) -> "Client":
        return cls(SpacetimeSettings.from_env(role=role))

    async def _post(self, route: str, body: bytes, content_type: str) -> bytes:
        url = f"{self.settings.http_url.rstrip('/')}/v1/database/{self.settings.database}/{route}"
        for attempt in range(self.attempts):
            try:
                return await asyncio.to_thread(
                    self.transport,
                    url,
                    body,
                    {
                        "Authorization": f"Bearer {self.settings.ingest_token}",
                        "Content-Type": content_type,
                    },
                    self.settings.timeout_s,
                )
            except SpacetimeError as error:
                if (
                    (error.status is not None and error.status not in {429, 500, 502, 503, 504})
                    or (error.category not in {"connection or timeout", "request rejected"})
                    or attempt + 1 == self.attempts
                ):
                    raise
                logger.warning("Database request retry %d", attempt + 1)
                await self.sleep(self.backoff * 2**attempt)
            except Exception:
                raise SpacetimeError("transport failed") from None
        raise AssertionError("Unreachable retry state")

    async def call(self, reducer: str, *args: object) -> None:
        """Retry only reducers whose arguments contain stable idempotency keys."""
        if not re.fullmatch(r"[a-z_]+", reducer):
            raise ValueError("Invalid reducer name")
        await self._post(
            f"call/{reducer}", json.dumps(args, allow_nan=False).encode(), "application/json"
        )

    async def sql(self, query: str) -> list[dict[str, Any]]:
        if not query.strip().upper().startswith("SELECT ") or len(query) > 8192:
            raise ValueError("Expected bounded SELECT query")
        raw = await self._post("sql", query.encode(), "text/plain")
        try:
            (result,) = json.loads(raw)
            columns = [element["name"]["some"] for element in result["schema"]["elements"]]
            if len(set(columns)) != len(columns):
                raise ValueError
            return [dict(zip(columns, row, strict=True)) for row in result["rows"]]
        except (ValueError, KeyError, TypeError):
            raise SpacetimeError("invalid SQL response") from None

    async def write(self, table: str, key: str, root_id: int, payload: dict[str, Any]) -> None:
        if table not in {
            "ingest_event",
            "weather_reading",
            "congestion_reading",
            "validation_result",
        }:
            raise ValueError("Unknown stream")
        await self.call(f"write_{table}", key, root_id, json.dumps(payload, allow_nan=False))
