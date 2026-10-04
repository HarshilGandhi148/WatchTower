"""Real local SpacetimeDB congestion workflow, exclusively a synthetic TEST database.

The weather responder below is a clearly identified TEST double, not the Weather
Agent. The production service, reducers, HTTP adapter and validation gateway are real.
"""

import asyncio
import json
from dataclasses import asdict, replace
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from uagents import Context
from uagents_core.types import DeliveryStatus, MsgStatus

from agents.congestion.feeds import stored_feeds
from agents.congestion.metrics import nearby_traffic
from agents.congestion.persistence import PersistentCongestionService
from agents.validation.orchestrator import Orchestrator
from agents.validation.protocol import ValidationGateway
from agents.validation.storage import ValidationStorage
from projection.aggregate_flow import FlowSnapshot, compare
from projection.assumptions import (
    CapacityBasis,
    CapacityScenario,
    ProjectionAssumptions,
    ResponseParameters,
)
from shared.contracts import AgentName, EvidenceQuality
from shared.messages import CandidateReadyMessage, CaseOpenedMessage, EnvelopeModel
from shared.orchestration import ValidationVerdict
from shared.persistence import WeatherFinding, storage_json
from shared.settings import AirportConfig, SpacetimeSettings
from shared.spacetimedb_adapter import SpacetimeAdapter, SpacetimeError, http_transport
from tests.fixtures.orchestration import TEST_ADDRESSES
from tests.fixtures.spacetimedb.congestion import packet


async def rejected(awaitable) -> None:
    try:
        await awaitable
    except SpacetimeError:
        return
    raise AssertionError("Invalid or unauthorized TEST request succeeded")


class FaultTransport:
    def __init__(self) -> None:
        self.failure = ""

    def __call__(self, url, body, headers, timeout):
        if url.endswith("/call/submit_flow_candidate") and self.failure == "before":
            self.failure = ""
            raise SpacetimeError("TEST database unavailable")
        result = http_transport(url, body, headers, timeout)
        if url.endswith("/call/submit_flow_candidate") and self.failure == "after":
            self.failure = ""
            raise SpacetimeError("TEST response lost after commit")
        return result


async def exercise_congestion(origin, database, admin, ingest, request_http, reducer) -> None:
    roles = {
        role: json.loads(request_http(f"{origin}/v1/identity"))
        for role in ("weather", "congestion", "validation", "unauthorized")
    }
    reducer("set_ingestion_permission", [ingest["identity"], True], admin["token"])
    for role in ("weather", "congestion", "validation"):
        reducer("set_service_permission", [roles[role]["identity"], role, True], admin["token"])

    def make(role):
        return SpacetimeAdapter(SpacetimeSettings(origin, database, roles[role]["token"]))

    ingestion = SpacetimeAdapter(SpacetimeSettings(origin, database, ingest["token"]))
    weather, validation, unauthorized = (
        make(role) for role in ("weather", "validation", "unauthorized")
    )
    faults = FaultTransport()
    congestion = SpacetimeAdapter(
        SpacetimeSettings(origin, database, roles["congestion"]["token"]), faults
    )
    at = datetime.now(UTC).replace(microsecond=0) - timedelta(seconds=2)
    adsb, faa = packet("adsb_lol", at, "TEST-adsb-one"), packet("faa", at, "TEST-faa-one")
    for adapter in (weather, validation, congestion, unauthorized):
        await rejected(adapter.call("ingest_feed_batch", adsb))
    await asyncio.gather(
        ingestion.call("ingest_feed_batch", adsb), ingestion.call("ingest_feed_batch", faa)
    )
    await ingestion.call("ingest_feed_batch", adsb)
    await rejected(
        ingestion.call("ingest_feed_batch", adsb | {"coverage_flags": ["conflicting ID"]})
    )

    durable = PersistentCongestionService(congestion)
    opening = await durable.cycle(at)
    snapshot = await congestion.load_snapshot(opening.snapshot_id)
    assert set(snapshot["evidence_ids"]) == set(opening.evidence_ids)
    assert nearby_traffic(stored_feeds(snapshot).adsb, at).total == 1
    # The aircraft retained by the foundation test is outside this selected batch.
    assert len(await ingestion.read_observations("aircraft_observations")) >= 2
    for evidence_id in opening.evidence_ids:
        await validation.load_record("evidence_records", "evidence_id", evidence_id)
    assert len(await ingestion.read_records("airport_events")) == 1

    ctx = MagicMock(spec=Context)
    ctx.send = AsyncMock(
        return_value=MsgStatus(
            status=DeliveryStatus.DELIVERED,
            detail="TEST transport",
            destination="TEST",
            endpoint="TEST",
        )
    )
    validate = AsyncMock(
        side_effect=[ValidationVerdict.REASSESS, ValidationVerdict.INSUFFICIENT_DATA]
    )
    persist = AsyncMock()
    coordinator = Orchestrator(TEST_ADDRESSES)
    storage = ValidationStorage(validation)
    gateway = ValidationGateway(coordinator, validate, persist, storage)
    await gateway.handle_open(
        ctx,
        TEST_ADDRESSES[AgentName.CONGESTION],
        CaseOpenedMessage(envelope=EnvelopeModel.from_envelope(opening)),
    )
    request = ctx.send.call_args.args[1].to_request()
    wx_request = await durable.assessment(request, datetime.now(UTC))
    assert wx_request.window_start_utc == at

    # Feed changes while weather works cannot alter existing evidence/window.
    newer = packet("adsb_lol", at + timedelta(seconds=1), "TEST-adsb-new", empty=True)
    await ingestion.call("ingest_feed_batch", newer)
    assert await congestion.load_snapshot(opening.snapshot_id) == snapshot
    await ingestion.call(
        "ingest_feed_batch", packet("adsb_lol", at - timedelta(seconds=1), "TEST-adsb-old")
    )
    second = await congestion.create_snapshot(
        "TEST-second-snapshot",
        "TEST-second-case",
        AirportConfig(),
        at,
        at + timedelta(minutes=15),
        at + timedelta(seconds=120),
    )
    assert nearby_traffic(stored_feeds(second).adsb, at).total == 0

    async def test_weather_double(wx_request, number):
        """TEST double: persists a clear assessment, makes no operational claim."""
        env = replace(
            wx_request.envelope,
            message_id=f"TEST-weather-reply-{number}",
            sender_agent=AgentName.WEATHER,
            created_at_utc=datetime.now(UTC).replace(microsecond=0),
            reply_to=wx_request.envelope.message_id,
        )
        finding = WeatherFinding(
            f"TEST-weather-{number}",
            env,
            number,
            wx_request.window_start_utc,
            wx_request.window_end_utc,
            "no_material_impact",
            EvidenceQuality.LIMITED,
            "TEST double only; Weather Agent is not implemented",
            assumptions=("TEST weather double",),
        )
        for adapter in (ingestion, congestion, validation, unauthorized):
            await rejected(adapter.save_weather(finding))
        await weather.save_weather(finding)
        await weather.save_weather(finding)
        await rejected(weather.save_weather(replace(finding, summary="TEST conflicting finding")))
        await durable.weather(env, finding.status, finding.finding_id, datetime.now(UTC))
        return finding

    finding = await test_weather_double(wx_request, 1)
    (preview_ready,) = durable.service.advance(datetime.now(UTC)).ready
    preview_candidate = durable.service.candidates[preview_ready.candidate_id]
    raw_candidate = json.loads(storage_json(preview_candidate))
    raw_ready = json.loads(storage_json(preview_ready))
    mutations = [
        {"weather_assessment_id": "TEST-missing-weather"},
        {"projection_id": "TEST-missing-projection"},
        {"envelope": raw_candidate["envelope"] | {"snapshot_id": "TEST-missing-snapshot"}},
        {"envelope": raw_candidate["envelope"] | {"airport_icao": "KJFK"}},
        {
            "envelope": raw_candidate["envelope"] | {"evidence_ids": ["TEST-missing-evidence"]},
            "traffic_metrics": [],
            "faa_event_ids": [],
        },
        {"traffic_metrics": [raw_candidate["traffic_metrics"][0] | {"unit": "meters"}]},
        {"traffic_metrics": [raw_candidate["traffic_metrics"][0] | {"value": True}]},
        {"window_start_utc": "2026-01-01T00:00:00.000Z"},
    ]
    for change in mutations:
        await rejected(
            congestion.call(
                "submit_flow_candidate", {"candidate": raw_candidate | change, "ready": raw_ready}
            )
        )
    await rejected(
        congestion.call(
            "submit_flow_candidate",
            {"candidate": raw_candidate, "ready": raw_ready | {"assessment_round": 2}},
        )
    )
    assert not await congestion.read_records("flow_candidates")
    assert not await congestion.read_records("pending_deliveries")
    send = AsyncMock()
    faults.failure = "before"
    await rejected(durable.advance(datetime.now(UTC), send))
    send.assert_not_awaited()
    assert not await congestion.read_records("flow_candidates")
    # Commit succeeds, response is lost. Candidate remains eligible for identical retry.
    faults.failure = "after"
    await rejected(durable.advance(datetime.now(UTC), send))
    send.assert_not_awaited()
    assert len(await congestion.read_records("flow_candidates")) == 1
    await durable.advance(datetime.now(UTC), send)
    ready = send.call_args.args[0]
    candidate = await validation.load_candidate(ready.candidate_id)
    assert (
        candidate.window_start_utc == at and candidate.window_end_utc == wx_request.window_end_utc
    )
    assert candidate.projection_id is None
    assert not any("demand" in metric.name for metric in candidate.traffic_metrics)
    assert candidate.weather_assessment_id == finding.finding_id
    assert len(await congestion.read_records("flow_candidates")) == 1
    assert len(await congestion.read_records("pending_deliveries")) == 1
    for adapter in (ingestion, weather, validation, unauthorized):
        await rejected(adapter.save_candidate(candidate, ready))
    await rejected(
        congestion.save_candidate(replace(candidate, resource="TEST conflicting ID"), ready)
    )

    message = CandidateReadyMessage.parse_raw(storage_json(ready))
    for _ in range(2):
        await gateway.handle_candidate(ctx, TEST_ADDRESSES[AgentName.CONGESTION], message)
    validate.assert_awaited_once()
    reports = await validation.read_records("validation_reports")
    assert len(reports) == 1
    report = json.loads(reports[0]["content_json"])
    assert report["candidate_id"] == ready.candidate_id
    assert report["snapshot_id"] == opening.snapshot_id
    assert len(report["checks"]) == 6
    await validation.call("store_validation_report", report)
    for adapter in (ingestion, weather, congestion, unauthorized):
        await rejected(adapter.call("store_validation_report", report))
    await rejected(validation.call("store_validation_report", report | {"suggestion": "conflict"}))

    assert not await congestion.pending_deliveries()
    row = await validation.load_record("pending_deliveries", "candidate_id", ready.candidate_id)
    assert row["status"] == "received"  # delivery, not approval
    for adapter in (ingestion, weather, congestion, unauthorized):
        await rejected(adapter.receipt(ready))
    # Correction keeps snapshot/window, but stores a distinct candidate.
    correction = ctx.send.call_args.args[1].to_request()
    second_wx = await durable.assessment(correction, datetime.now(UTC))
    assert (second_wx.window_start_utc, second_wx.window_end_utc) == (
        wx_request.window_start_utc,
        wx_request.window_end_utc,
    )
    assert second_wx.envelope.snapshot_id == opening.snapshot_id
    await test_weather_double(second_wx, 2)
    crash = AsyncMock(side_effect=OSError("TEST process stops after commit"))
    await durable.advance(datetime.now(UTC), crash)
    ready_two = crash.call_args.args[0]
    assert ready_two.candidate_id != ready.candidate_id
    assert len(await congestion.read_records("flow_candidates")) == 2
    restarted = PersistentCongestionService(congestion)
    resumed = AsyncMock()
    await restarted.advance(datetime.now(UTC), resumed)
    resumed.assert_awaited_once_with(ready_two)
    await gateway.handle_candidate(
        ctx,
        TEST_ADDRESSES[AgentName.CONGESTION],
        CandidateReadyMessage.parse_raw(storage_json(ready_two)),
    )
    assert validate.await_count == 2
    assert len(await validation.read_records("validation_reports")) == 2
    assert not await congestion.pending_deliveries()
    assert (await congestion.load_record("evidence_snapshots", "snapshot_id", opening.snapshot_id))[
        "closed"
    ]
    # Entire bureau restart cannot reopen a stale case from a replayed opening.
    restarted_gateway = ValidationGateway(
        Orchestrator(TEST_ADDRESSES), validate, persist, storage, recover_on_start=True
    )
    before = ctx.send.await_count
    await restarted_gateway.handle_open(
        ctx,
        TEST_ADDRESSES[AgentName.CONGESTION],
        CaseOpenedMessage(envelope=EnvelopeModel.from_envelope(opening)),
    )
    assert ctx.send.await_count == before

    # Ingestion racing a snapshot yields either whole batch, never mixed membership.
    at2 = datetime.now(UTC).replace(microsecond=0)
    third = packet("adsb_lol", at2, "TEST-adsb-third")
    _, frozen = await asyncio.gather(
        ingestion.call("ingest_feed_batch", third),
        congestion.create_snapshot(
            "TEST-racing-snapshot",
            "TEST-racing-case",
            AirportConfig(),
            at2,
            at2 + timedelta(minutes=15),
            at2 + timedelta(seconds=120),
        ),
    )
    batch = next(b for b in frozen["batches"] if b["payload"]["provider"] == "adsb_lol")
    assert len(batch["payload"]["snapshot"]["observations"]) in (0, 1)
    assert nearby_traffic(stored_feeds(frozen).adsb, at2).total == len(
        batch["payload"]["snapshot"]["observations"]
    )
    for adapter in (ingestion, weather, validation, unauthorized):
        await rejected(
            adapter.call(
                "create_evidence_snapshot",
                {
                    "snapshot_id": "TEST-denied",
                    "case_id": "TEST-denied",
                    "configuration": asdict(AirportConfig()),
                    "window_start_utc": at2,
                    "window_end_utc": at2 + timedelta(minutes=15),
                    "expires_at_utc": at2 + timedelta(seconds=120),
                },
            )
        )

    # Supported TEST projection requires stored, fresh airport departure records.
    flight = {
        "batch_id": "TEST-flight-batch",
        "schema_version": 1,
        "data_mode": "live",
        "provider": "flightaware",
        "source_ref": "https://example.test/TEST-departures",
        "configuration": asdict(AirportConfig()),
        "fetched_at_utc": at2,
        "records": [
            {
                "flight_id": f"TEST-flight-{n}",
                "direction": "departure",
                "time_utc": at2 + timedelta(minutes=n % 15),
                "time_kind": "estimated_runway",
                "association": "confirmed_record",
                "cancelled": False,
            }
            for n in range(30)
        ],
    }
    await ingestion.call("ingest_flight_records", flight)
    projected_snapshot = await congestion.create_snapshot(
        "TEST-projection-snapshot",
        "TEST-projection-case",
        AirportConfig(),
        at2,
        at2 + timedelta(minutes=15),
        at2 + timedelta(seconds=120),
    )
    flow = FlowSnapshot(
        "TEST-projection-snapshot",
        at2,
        0,
        0,
        (2,) * 15,
        "TEST eligibility estimate from confirmed departure records",
    )
    assumptions = ProjectionAssumptions(
        capacity=CapacityScenario("TEST hypothetical capacity", 60, CapacityBasis.HYPOTHETICAL)
    )
    response = ResponseParameters(30, 0, 15)
    result = compare(flow, assumptions, response)
    projection = {
        "projection_id": "TEST-projection",
        "snapshot_id": flow.snapshot_id,
        "model_version": result.model_version,
        "evidence_ids": ["TEST-flight-batch"],
        "input": flow,
        "assumptions": assumptions,
        "response": response,
        "result": result,
    }
    observations_before = await ingestion.read_observations("aircraft_observations")
    for adapter in (ingestion, weather, validation, unauthorized):
        await rejected(adapter.call("store_projection", projection))
    await congestion.call("store_projection", projection)
    await congestion.call("store_projection", projection)
    saved = await validation.load_record("projection_results", "projection_id", "TEST-projection")
    assert (
        json.loads(saved["content_json"])["result"]["input_fingerprint"] == result.input_fingerprint
    )
    assert await ingestion.read_observations("aircraft_observations") == observations_before
    assert "TEST-flight-batch" in projected_snapshot["evidence_ids"]
    assert await storage.projection("TEST-projection") == result
    tampered = json.loads(storage_json(projection))
    tampered["projection_id"] = "TEST-invented-demand"
    tampered["input"]["demand_per_minute"][0] = 999
    await rejected(congestion.call("store_projection", tampered))
    await rejected(
        congestion.call(
            "store_projection",
            projection | {"projection_id": "TEST-unsupported", "evidence_ids": ["TEST-adsb-third"]},
        )
    )
    # Prune old live rows without deleting copied evidence for the saved candidates.
    stale = packet("adsb_lol", at2 - timedelta(hours=1), "TEST-stale-pruning")
    stale["snapshot"]["observations"][0]["aircraft_id"] = "a00002"
    await ingestion.call("ingest_feed_batch", stale)
    await ingestion.call("prune_live_observations")
    assert not any(
        row["aircraft_id"] == "a00002"
        for row in await ingestion.read_observations("aircraft_observations")
    )
    assert await validation.load_candidate(ready.candidate_id) == candidate
    for evidence_id in candidate.envelope.evidence_ids:
        await validation.load_record("evidence_records", "evidence_id", evidence_id)
    # Server rejects expired submissions without any timing-dependent sleep.
    expired = replace(
        candidate,
        envelope=replace(
            candidate.envelope,
            message_id="TEST-expired",
            created_at_utc=at2 - timedelta(minutes=10),
            valid_until_utc=at2 - timedelta(minutes=5),
        ),
    )
    expired_ready = replace(ready, candidate_id="TEST-expired", envelope=expired.envelope)
    await rejected(congestion.save_candidate(expired, expired_ready))
