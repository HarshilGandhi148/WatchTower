"""Synthetic TEST feed packets, for isolated databases and unit tests only."""

import json
from dataclasses import asdict
from datetime import datetime, timedelta
from typing import Any

from data.adsb import parse_point_response
from data.faa_status import parse_status_xml
from shared.persistence import storage_json
from shared.settings import AirportConfig


def packet(provider: str, at: datetime, batch_id: str, *, empty: bool = False) -> dict[str, Any]:
    if provider == "adsb_lol":
        snapshot = parse_point_response(
            {
                "now": at.timestamp(),
                "total": 0 if empty else 1,
                "ac": []
                if empty
                else [
                    {
                        "hex": "a00001",
                        "lat": 42.22,
                        "lon": -83.35,
                        "alt_baro": 3000,
                        "seen_pos": 0,
                        "gs": 160,
                        "track": 180,
                        "flight": "TEST001",
                    }
                ],
            },
            at,
        )
    else:
        body = (
            "<AIRPORT_STATUS_INFORMATION><Update_Time>"
            f"{at.strftime('%a %b %d %H:%M:%S %Y')} UTC</Update_Time>"
        )
        if not empty:
            body += (
                "<Delay_type><Name>Ground Stop</Name><Ground_Stop_List><Program>"
                "<ARPT>DTW</ARPT><Reason>TEST only</Reason>"
                "</Program></Ground_Stop_List></Delay_type>"
            )
        body += "</AIRPORT_STATUS_INFORMATION>"
        snapshot = parse_status_xml(body.encode(), at)
    return json.loads(
        storage_json(
            {
                "schema_version": 1,
                "data_mode": "live",
                "batch_id": batch_id,
                "provider": provider,
                "source_ref": "https://example.test/TEST",
                "configuration": AirportConfig(),
                "snapshot": asdict(snapshot),
                "coverage_flags": [],
            }
        )
    )


def snapshot_fixture(at: datetime, *, adsb: bool = True, faa: bool = True) -> dict[str, Any]:
    batches = [
        packet(provider, at, f"TEST-{provider}-batch")
        for provider, enabled in (("adsb_lol", adsb), ("faa", faa))
        if enabled
    ]
    ids = [b["batch_id"] for b in batches]
    for b in batches:
        if b["provider"] == "faa":
            ids += [f"{b['batch_id']}:{e['event_key']}" for e in b["snapshot"]["events"]]
    return json.loads(
        storage_json(
            {
                "snapshot_id": "TEST-snapshot",
                "case_id": "TEST-case",
                "configuration": AirportConfig(),
                "window_start_utc": at,
                "window_end_utc": at + timedelta(minutes=15),
                "expires_at_utc": at + timedelta(seconds=120),
                "cutoff_at_utc": at,
                "evidence_ids": ids,
                "batches": [{"batch_id": b["batch_id"], "payload": b} for b in batches],
                "weather": [],
                "health": [{"provider": b["provider"], "status": "ok"} for b in batches],
                "complete": False,
                "limitations": ["airport-associated demand unavailable"],
            }
        )
    )
