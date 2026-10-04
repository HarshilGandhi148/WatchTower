"""Real stored-snapshot congestion candidates pass the validator end to end (TEST inputs).

Cross-owner integration test. The real CongestionService builds a projected candidate
from a TEST stored snapshot, and the real Orchestrator routes it. Congestion's stored
records (candidate, delivery, projection payload) go into an in-memory TEST store, and
the validator loads them with its real loader and checks them with validate_packet. No
agents, servers, providers or databases are started.
"""

import json
from dataclasses import replace
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase

from agents.congestion.candidates import ReviewThresholds
from agents.congestion.feeds import stored_feeds
from agents.congestion.projections import ProjectionSettings
from agents.congestion.service import CongestionService
from agents.validation.loader import load_packet
from agents.validation.orchestrator import Orchestrator
from agents.validation.packet import ValidationPacket
from agents.validation.service import validate_packet
from agents.validation.storage import ValidationStorage
from shared.contracts import ActionType, AdvisoryType, AgentName, CandidateStatus, MessageEnvelope
from shared.orchestration import CasePhase, ValidationVerdict
from shared.persistence import ready_from_dict, storage_json
from tests.fixtures.orchestration import TEST_ADDRESSES
from tests.fixtures.validation import storage as fs

CONGESTION = TEST_ADDRESSES[AgentName.CONGESTION]
SETTINGS = ProjectionSettings(capacity_per_hour=30, backlog_upstream=6, backlog_runway_queue=4)


class CongestionValidationTests(IsolatedAsyncioTestCase):
    async def run_case(self, service: CongestionService) -> tuple[Orchestrator, ValidationPacket]:
        at = fs.at_now()
        store, _ = fs.stored_case(at)
        del store.tables["projection_results"][fs.PROJECTION_ID]  # congestion stores its own
        snapshot = json.loads(store.tables["evidence_snapshots"][fs.SNAPSHOT_ID]["content_json"])
        coordinator = Orchestrator(TEST_ADDRESSES)
        opening = service.start_cycle(stored_feeds(snapshot), at)
        dispatch = coordinator.open_case(CONGESTION, opening, now=at)
        assert dispatch is not None
        weather_request = service.on_assessment_request(dispatch.request, at)
        reply = MessageEnvelope(
            schema_version=1,
            message_id="TEST-weather-reply-1",
            case_id=opening.case_id,
            sender_agent=AgentName.WEATHER,
            created_at_utc=at,
            snapshot_id=opening.snapshot_id,
            evidence_ids=(),
            airport_icao="KDTW",
            valid_until_utc=at + timedelta(minutes=1),
            reply_to=weather_request.envelope.message_id,
        )
        service.on_weather_reply(reply, "no_material_impact", fs.WEATHER_ID, at)
        (ready,) = service.advance(at + timedelta(seconds=1)).ready
        candidate = service.candidates[ready.candidate_id]
        if candidate.projection_id is not None:
            payload = service.projections[candidate.projection_id].payload()
            store.put(
                "projection_results",
                {
                    "projection_id": candidate.projection_id,
                    "snapshot_id": opening.snapshot_id,
                    "content_json": storage_json(payload),
                },
            )
        fs.store_candidate(store, candidate, ready)
        sent = ready_from_dict(json.loads(storage_json(ready)))  # the stored delivery copy
        coordinator.receive_candidate(CONGESTION, sent, now=at + timedelta(seconds=1))
        storage = ValidationStorage(store)  # type: ignore[arg-type]
        await storage.retrieve(sent)  # the partner's retrieval hook accepts the records
        packet = await load_packet(
            store,  # type: ignore[arg-type]
            storage,
            opening,
            sent,
            at + timedelta(seconds=2),
        )
        return coordinator, packet

    async def test_projected_release_review_is_published_with_matching_numbers(self) -> None:
        service = CongestionService(
            projection=SETTINGS, thresholds=ReviewThresholds(peak_runway_queue_aircraft=2)
        )
        coordinator, packet = await self.run_case(service)
        candidate = packet.candidate
        assert candidate is not None and packet.projection is not None
        self.assertIs(candidate.status, CandidateStatus.CHANGE_WORTH_REVIEWING)
        outcome = validate_packet(packet)
        self.assertIs(outcome.verdict, ValidationVerdict.PUBLISH, outcome.findings)
        advisory = outcome.advisory
        assert advisory is not None
        self.assertIs(advisory.advisory_type, AdvisoryType.DEPARTURE_RELEASE_REVIEW)
        self.assertIs(advisory.action_type, ActionType.REVIEW_RELEASE_RATE)
        self.assertIn(candidate.projection_id, advisory.evidence_ids)
        comparison = packet.projection.comparison
        assert comparison.baseline and comparison.proposal
        self.assertIn(f"{comparison.baseline.metrics.peak_runway_queue} aircraft", advisory.reason)
        self.assertIn("30 aircraft/hour", advisory.suggested_response)
        self.assertTrue(any("operator-configured" in a for a in advisory.assumptions))
        coordinator.finish_validation(packet.ready, outcome.verdict, now=packet.now_utc)
        case = coordinator.get_case(candidate.envelope.case_id)
        self.assertIs(case.phase, CasePhase.READY_TO_PUBLISH)

    async def test_tampered_congestion_numbers_are_rejected(self) -> None:
        _, packet = await self.run_case(CongestionService(projection=SETTINGS))
        candidate = packet.candidate
        assert candidate is not None
        metrics = tuple(
            replace(m, value=m.value - 1) if m.name == "projected_peak_runway_queue_baseline" else m
            for m in candidate.traffic_metrics
        )
        tampered = replace(packet, candidate=replace(candidate, traffic_metrics=metrics))
        outcome = validate_packet(tampered)
        self.assertIs(outcome.verdict, ValidationVerdict.REJECT)
        self.assertIn("projected_value_mismatch", {f.code for f in outcome.findings})

    async def test_without_projection_settings_no_flow_action_is_published(self) -> None:
        _, packet = await self.run_case(CongestionService())
        candidate = packet.candidate
        assert candidate is not None
        self.assertIsNone(candidate.projection_id)
        outcome = validate_packet(packet)
        if outcome.advisory is not None:
            self.assertIs(outcome.advisory.action_type, ActionType.INFORMATION_NOTICE)
            self.assertEqual(outcome.advisory.action_parameters, ())
        else:
            self.assertIn(
                outcome.verdict, (ValidationVerdict.REJECT, ValidationVerdict.INSUFFICIENT_DATA)
            )
