"""Conservative weather availability reading; no LLM or operational capacity claims."""

from datetime import datetime
from typing import Any

from shared.bridge import StreamEvent, fresh
from watchtower_client import Client


def assess(payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    reading = payload.get("weather")
    available = isinstance(reading, dict) and fresh(reading.get("observed_at_utc"), now, 90 * 60)
    return {
        "schema_version": 1,
        "status": "available" if available else "unknown",
        "limitations": [] if available else ["Weather observation missing or stale"],
        "wind_speed_kt": reading.get("wind_speed_kt")
        if available and isinstance(reading, dict)
        else None,
        "visibility_m": reading.get("visibility_m")
        if available and isinstance(reading, dict)
        else None,
        "ceiling_ft": reading.get("ceiling_ft")
        if available and isinstance(reading, dict)
        else None,
        "capacity_aircraft_per_hour": None,
    }


async def handle(event: StreamEvent, client: Client) -> None:
    await client.write(
        "weather_reading",
        f"weather:{event.row_id}",
        event.row_id,
        assess(event.payload, datetime.fromisoformat(event.created_at_utc)),
    )
