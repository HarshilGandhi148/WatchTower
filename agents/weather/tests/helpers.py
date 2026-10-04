"""TEST-only weather packets and explicit injectable stores."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from data.awc import parse_metar_response, parse_taf_response
from shared.contracts import AgentName, MessageEnvelope, WeatherAssessmentRequest, WeatherSnapshot

NOW = datetime(2026, 10, 4, 5, 0, tzinfo=UTC)
FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "awc"


def metar_payload():  # type: ignore[no-untyped-def]
    return json.loads((FIXTURES / "metar.json").read_text())[0]


def taf_payload():  # type: ignore[no-untyped-def]
    return json.loads((FIXTURES / "taf.json").read_text())[0]


def snapshot(*, metar=None, taf=None, now=NOW, snapshot_id="snap-1"):  # type: ignore[no-untyped-def]
    metars = parse_metar_response([metar or metar_payload()], now) if metar is not False else ()
    tafs = parse_taf_response([taf or taf_payload()], now) if taf is not False else ()
    ids = tuple(r.evidence_id for r in (*metars, *tafs))
    return WeatherSnapshot(
        snapshot_id=snapshot_id, cutoff_utc=now, evidence_ids=ids, metars=metars, tafs=tafs
    )


def request(snap, *, now=NOW, attempt=1, message_id="request-1", case_id="case-1", **changes):  # type: ignore[no-untyped-def]
    envelope = MessageEnvelope(
        schema_version=1,
        message_id=message_id,
        case_id=case_id,
        sender_agent=AgentName.CONGESTION,
        created_at_utc=now,
        snapshot_id=snap.snapshot_id,
        evidence_ids=snap.evidence_ids,
        airport_icao="KDTW",
        valid_until_utc=now + timedelta(seconds=120),
        correlation_id="round-1",
    )
    return WeatherAssessmentRequest(
        envelope,
        changes.get("start", now),
        changes.get("end", now + timedelta(minutes=15)),
        attempt,
    )
