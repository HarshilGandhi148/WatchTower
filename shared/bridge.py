"""Shared persisted-stream event contract and freshness helpers."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

STREAMS = frozenset({"ingest_event", "weather_reading", "congestion_reading", "validation_result"})


@dataclass(frozen=True)
class StreamEvent:
    table: str
    row_id: int
    root_id: int
    created_at_utc: str
    payload: dict[str, Any]


def fresh(value: object, now: datetime, max_age_s: int = 300) -> bool:
    if not isinstance(value, str):
        return False
    try:
        at = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return at.utcoffset() == UTC.utcoffset(at) and 0 <= (now - at).total_seconds() <= max_age_s
    except (ValueError, TypeError):
        return False
