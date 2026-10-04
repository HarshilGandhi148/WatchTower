"""Regenerate TEST replay captures with real congestion calculations and report checks.

Weather and message delivery are explicit test doubles, not a live bureau recording.
Run from the repository root: python -m tests.fixtures.replay.generate
"""

import json
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from agents.congestion.service import CongestionService
from agents.congestion.tests.test_service import NOW, validation_request, weather_reply
from agents.validation.reports import evaluate
from data.evidence_inputs import stored_feeds
from shared.contracts import AgentName, EvidenceQuality
from shared.persistence import WeatherFinding, storage_json
from tests.fixtures.spacetimedb.congestion import snapshot_fixture


def capture(name: str) -> dict:  # type: ignore[type-arg]
    snapshot = snapshot_fixture(NOW, faa=False)
    snapshot["snapshot_id"] = f"TEST-{name}-snapshot"
    snapshot["case_id"] = f"TEST-{name}-case"
    source = snapshot["batches"][0]["payload"]["snapshot"]
    first = source["observations"][0]
    source["observations"] = [
        {
            **first,
            "aircraft_id": f"a{i:05x}",
            "latitude_deg": 42.05 + (i % 8) * 0.035,
            "longitude_deg": -83.55 + (i // 8) * 0.09,
        }
        for i in range(34)
    ]
    source["reported_total"] = 34
    if name == "outage":
        snapshot["health"][0]["status"] = "failed"
    snapshot["evidence_ids"].append("TEST-weather-observation")
    snapshot["weather"] = [
        {
            "evidence_id": "TEST-weather-observation",
            "provider": "awc",
            "observed_at_utc": NOW.isoformat(),
            "wind_speed_kt": 18,
            "wind_gust_kt": 27,
            "visibility_m": 8000,
            "basis": "TEST synthetic METAR",
        }
    ]
    rows: dict[str, list] = {
        k: [] for k in ("requests", "findings", "candidates", "deliveries", "reports")
    }  # type: ignore[type-arg]
    service = CongestionService()
    opening = service.start_cycle(stored_feeds(snapshot), NOW)
    last = None
    for round_ in range(1, 3 if name == "correction" else 2):
        offset = 0 if round_ == 1 else 12
        request = validation_request(
            opening, round_, reply_to=last, created_at_utc=NOW + timedelta(seconds=offset + 1)
        )
        wx = service.on_assessment_request(request, NOW + timedelta(seconds=offset + 2))
        envelope = weather_reply(
            wx.envelope.message_id, opening, NOW + timedelta(seconds=offset + 3), round_
        )
        finding = WeatherFinding(
            f"TEST-{name}-weather-{round_}",
            replace(
                envelope, sender_agent=AgentName.WEATHER, evidence_ids=("TEST-weather-observation",)
            ),
            round_,
            NOW,
            NOW + timedelta(minutes=15),
            "impact",
            EvidenceQuality.LIMITED,
            "TEST weather assessment: gusts of 27 kt. Review the flow window; "
            "no official capacity change is inferred.",
            assumptions=("Weather test double using synthetic TEST observation.",),
        )
        service.on_weather_reply(
            envelope, finding.status, finding.finding_id, NOW + timedelta(seconds=offset + 3)
        )
        (ready,) = service.advance(NOW + timedelta(seconds=offset + 4)).ready
        service.mark_persisted(ready)
        candidate = service.candidates[ready.candidate_id]
        if name == "correction" and round_ == 1:
            candidate = replace(
                candidate,
                traffic_metrics=(
                    replace(candidate.traffic_metrics[0], value=999),
                    *candidate.traffic_metrics[1:],
                ),
            )
        finding_json = json.loads(storage_json(finding))
        report = evaluate(
            candidate, snapshot, finding_json, round_, NOW + timedelta(seconds=offset + 6)
        )
        rows["requests"].append({"contentJson": storage_json(request)})
        rows["findings"].append({"contentJson": storage_json(finding)})
        rows["candidates"].append({"contentJson": storage_json(candidate)})
        rows["deliveries"].append(
            {
                "contentJson": storage_json(ready),
                "status": "received",
                "atUtc": (NOW + timedelta(seconds=offset + 5)).isoformat(),
            }
        )
        rows["reports"].append({"contentJson": storage_json(report)})
        last = ready.candidate_id
    return {
        "id": name,
        "capturedAtUtc": NOW.isoformat(),
        "provenance": (
            "TEST synthetic inputs; real CongestionService and validator evaluate; "
            "weather and delivery test doubles"
        ),
        "snapshot": snapshot,
        "rows": rows,
        "aircraft": [
            {
                "id": r["aircraft_id"],
                "label": "TEST " + r["aircraft_id"],
                "latitudeDeg": r["latitude_deg"],
                "longitudeDeg": r["longitude_deg"],
                "trackDeg": 180,
                "observedAtUtc": NOW.isoformat(),
                "dtwAssociated": False,
            }
            for r in source["observations"]
        ]
        if name != "outage"
        else [],
    }


if __name__ == "__main__":
    target = Path("frontend/tests/replay-captures.json")
    target.write_text(
        storage_json([capture(n) for n in ("concern", "correction", "outage")]) + "\n"
    )
