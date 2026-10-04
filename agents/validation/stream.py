"""Join same-root persisted readings, retaining explicit input limitations."""

import json

from shared.bridge import StreamEvent
from watchtower_client import Client


async def handle(event: StreamEvent, client: Client) -> None:
    root = event.row_id if event.table == "ingest_event" else event.root_id
    findings = []
    for table in ("weather_reading", "congestion_reading"):
        rows = await client.sql(f"SELECT * FROM {table} WHERE root_id = {root} LIMIT 2")
        if not rows:
            return  # A later specialist event completes the join.
        if len(rows) != 1:
            raise ValueError("Ambiguous specialist readings")
        findings.append(json.loads(rows[0]["payload_json"]))
    limitations = sorted({item for finding in findings for item in finding["limitations"]})
    await client.write(
        "validation_result",
        f"validation:{root}",
        root,
        {
            "schema_version": 1,
            "outcome": "insufficient_data",
            "suggestion": "Review source availability; departure advice unavailable",
            "limitations": limitations,
            "reading_ids": [f"weather:{root}", f"congestion:{root}"],
        },
    )
