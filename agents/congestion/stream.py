"""Read traffic availability from a frozen ingest row; missing demand stays unknown."""

from datetime import datetime
from typing import Any

from shared.bridge import StreamEvent, fresh
from watchtower_client import Client


def assess(payload: dict[str, Any], now: datetime) -> dict[str, Any]:
    traffic = payload.get("traffic")
    available = (
        isinstance(traffic, dict)
        and traffic.get("fetch_succeeded") is True
        and fresh(traffic.get("fetched_at_utc"), now)
        and isinstance(traffic.get("observations"), list)
    )
    if available and isinstance(traffic, dict):
        # A fresh successful fetch does not make retained/stale positions usable.
        available = all(
            isinstance(row, dict)
            and fresh(row.get("observed_at_utc"), now, 60)
            and isinstance(row.get("latitude_deg"), (int, float))
            and not isinstance(row.get("latitude_deg"), bool)
            and -90 <= row["latitude_deg"] <= 90
            and isinstance(row.get("longitude_deg"), (int, float))
            and not isinstance(row.get("longitude_deg"), bool)
            and -180 <= row["longitude_deg"] <= 180
            for row in traffic["observations"]
        )
    return {
        "schema_version": 1,
        "status": "available" if available else "unknown",
        "nearby_aircraft": len(traffic["observations"])
        if available and isinstance(traffic, dict)
        else None,
        "departure_demand": None,
        "projection": None,
        "limitations": ["Departure demand unavailable"]
        + ([] if available else ["Traffic fetch/positions missing, failed or stale"]),
    }


async def handle(event: StreamEvent, client: Client) -> None:
    await client.write(
        "congestion_reading",
        f"congestion:{event.row_id}",
        event.row_id,
        assess(event.payload, datetime.fromisoformat(event.created_at_utc)),
    )
