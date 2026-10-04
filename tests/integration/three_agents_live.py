"""Real specialist services and DB, with explicit TEST in-process message delivery.

The SDK factories/handlers have separate wiring checks; this gate verifies all stored
request/finding/candidate/report/receipt transitions, with no weather reply double.
"""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

from uagents import Context
from uagents_core.types import DeliveryStatus, MsgStatus

from agents.congestion.persistence import PersistentCongestionService
from agents.validation.orchestrator import Orchestrator
from agents.validation.protocol import ValidationGateway
from agents.validation.reports import ReportValidator
from agents.validation.storage import ValidationStorage
from agents.weather.assessment import assess_weather
from agents.weather.persistence import WeatherStorage
from agents.weather.service import WeatherDependencies, WeatherService
from data.weather_ingestion import normalized_metar
from shared.contracts import (
    AgentName,
    ForecastGroup,
    MetarEvidence,
    TafEvidence,
    WeatherConditions,
    WeatherRecord,
)
from shared.messages import CandidateReadyMessage, CaseOpenedMessage, EnvelopeModel
from tests.fixtures.orchestration import TEST_ADDRESSES
from tests.fixtures.spacetimedb.congestion import packet


async def exercise_case(
    ingestion, weather, congestion, validation, record=None, expected="insufficient_data"
):
    now = datetime.now(UTC).replace(microsecond=0)
    await ingestion.ingest_weather_record(
        record
        or WeatherRecord(
            observed_at_utc=now,
            fetched_at_utc=now,
            source_ref="https://example.test/TEST-metar",
            wind_direction_deg=180,
            wind_speed_kt=5,
            visibility_m=16093.44,
        )
    )
    durable = PersistentCongestionService(congestion)
    opening = await durable.cycle(now)
    ctx = MagicMock(spec=Context)
    ctx.send = AsyncMock(
        return_value=MsgStatus(
            status=DeliveryStatus.DELIVERED,
            detail="TEST in-process delivery",
            destination="TEST",
            endpoint="TEST",
        )
    )
    storage = ValidationStorage(validation)
    gateway = ValidationGateway(
        Orchestrator(TEST_ADDRESSES), ReportValidator(storage), AsyncMock(), storage
    )
    await gateway.handle_open(
        ctx,
        TEST_ADDRESSES[AgentName.CONGESTION],
        CaseOpenedMessage(envelope=EnvelopeModel.from_envelope(opening)),
    )
    request = ctx.send.call_args.args[1].to_request()
    weather_request = await durable.assessment(request, datetime.now(UTC))
    weather_store = WeatherStorage(weather)
    weather_snapshot = await weather_store.load_snapshot(weather_request.envelope.snapshot_id)
    assess_weather(weather_request, weather_snapshot, datetime.now(UTC))
    weather_service = WeatherService(
        WeatherDependencies(weather_store.load_snapshot, weather_store.persist_assessment)
    )
    reply = await weather_service.handle(weather_request)
    assert reply.status == expected
    await durable.weather(
        reply.envelope.to_envelope(), reply.status, reply.assessment_id, datetime.now(UTC)
    )
    sent = []

    async def deliver(ready):
        sent.append(ready)
        await gateway.handle_candidate(
            ctx,
            TEST_ADDRESSES[AgentName.CONGESTION],
            CandidateReadyMessage(
                envelope=EnvelopeModel.from_envelope(ready.envelope),
                candidate_id=ready.candidate_id,
                weather_assessment_id=ready.weather_assessment_id,
                assessment_round=ready.assessment_round,
                projection_id=ready.projection_id,
            ),
        )

    await durable.advance(datetime.now(UTC), deliver)
    assert len(sent) == 1, "Insufficient weather must still reach validation"
    ready = sent[0]
    finding = await validation.load_record("agent_findings", "finding_id", reply.assessment_id)
    assert json.loads(finding["content_json"])["status"] == expected
    report = await validation.load_record(
        "validation_reports", "report_id", f"report:{ready.candidate_id}"
    )
    result = json.loads(report["content_json"])
    assert result["outcome"] == (
        "insufficient_data" if expected == "insufficient_data" else "supported"
    )
    delivery = await validation.load_record(
        "pending_deliveries", "candidate_id", ready.candidate_id
    )
    assert delivery["status"] == "received"
    assert gateway.coordinator.get_case(opening.case_id).notified
    await weather_service.close()
    return result


async def exercise_three_agents(ingestion, weather, congestion, validation):
    partial = await exercise_case(ingestion, weather, congestion, validation)
    results = [partial]
    for concern in (False, True):
        now = datetime.now(UTC)
        now = now.replace(microsecond=now.microsecond // 1000 * 1000)
        # Simulate a client clock ahead of the server: the transaction must reject
        # until the unchanged retained evidence fits a newly captured cutoff.
        fetched = now + timedelta(seconds=1) if not concern else now
        c = WeatherConditions(
            wind_direction_deg=180,
            wind_speed_kt=25 if concern else 5,
            wind_gust_kt=40 if concern else None,
            visibility_m=700 if concern else 16093.44,
            ceiling_ft=200 if concern else None,
            sky_known=True,
            clear_skies=not concern,
            weather_known=True,
            weather_codes=("SN",) if concern else (),
        )
        metar = MetarEvidence(
            evidence_id="TEST-metar",
            observed_at_utc=now,
            fetched_at_utc=fetched,
            source_ref="https://example.test/TEST-metar",
            raw_text="TEST METAR",
            conditions=c,
        )
        start, end = now - timedelta(hours=1), now + timedelta(hours=24)
        taf = TafEvidence(
            evidence_id="TEST-taf",
            issued_at_utc=start,
            fetched_at_utc=fetched,
            start_utc=start,
            end_utc=end,
            source_ref="https://example.test/TEST-taf",
            raw_text="TEST TAF",
            groups=(ForecastGroup(start, end, c),),
        )
        await ingestion.call(
            "ingest_feed_batch", packet("adsb_lol", now, f"TEST-rich-weather-{concern}")
        )
        result = await exercise_case(
            ingestion,
            weather,
            congestion,
            validation,
            normalized_metar(metar, (taf,)),
            "impact" if concern else "no_material_impact",
        )
        results.append(result)
        assert "normalized_METAR_only" not in result["limitations"]
        assert "historical_window_coverage" not in result["limitations"]
        assert "nearby aircraft within" in result["suggestion"]
    assert len({r["suggestion"] for r in results}) == 3
