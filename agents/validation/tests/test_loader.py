"""Stored records → ValidationPacket, using an in-memory TEST store (no database)."""

import json
from dataclasses import replace
from datetime import timedelta
from unittest import IsolatedAsyncioTestCase

from agents.validation.loader import OUTSIDE_SNAPSHOT, load_packet
from agents.validation.packet import EvidenceKind
from agents.validation.service import validate_packet
from agents.validation.storage import ValidationStorage
from shared.contracts import AgentName
from shared.orchestration import AssessmentRequest, ValidationVerdict
from shared.spacetimedb_adapter import SpacetimeError
from tests.fixtures.validation import storage as fs


class LoaderTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.at = fs.at_now()
        self.store, self.opening = fs.stored_case(self.at)
        request = AssessmentRequest(
            replace(
                self.opening,
                message_id="TEST-request-1",
                sender_agent=AgentName.VALIDATION,
                correlation_id=self.opening.message_id,
                reply_to=self.opening.message_id,
            ),
            1,
        )
        self.candidate, self.ready = fs.candidate_for(request, self.at)
        fs.store_candidate(self.store, self.candidate, self.ready)

    async def load(self):  # type: ignore[no-untyped-def]
        return await load_packet(
            self.store,  # type: ignore[arg-type]
            ValidationStorage(self.store),  # type: ignore[arg-type]
            self.opening,
            self.ready,
            self.at + timedelta(seconds=3),
        )

    async def test_stored_case_loads_and_publishes(self) -> None:
        packet = await self.load()
        self.assertEqual(packet.candidate, self.candidate)
        self.assertEqual(packet.snapshot_cutoff_utc, self.at)
        assert packet.weather is not None and packet.projection is not None
        self.assertEqual(packet.weather.status, "no_material_impact")
        self.assertEqual(packet.projection.projection_id, fs.PROJECTION_ID)
        self.assertIs(validate_packet(packet).verdict, ValidationVerdict.PUBLISH)

    async def test_evidence_comes_from_the_copied_snapshot(self) -> None:
        evidence = (await self.load()).evidence
        kinds = {key: record.kind for key, record in evidence.items()}
        self.assertIs(kinds[fs.ADSB_BATCH], EvidenceKind.TRAFFIC)
        self.assertIs(kinds[fs.FLIGHT_BATCH], EvidenceKind.DEMAND)
        self.assertIs(kinds[fs.PROJECTION_ID], EvidenceKind.PROJECTION)
        events = [k for k in evidence if k.startswith("TEST-faa-batch:")]
        self.assertTrue(events)
        self.assertTrue(all(evidence[k].kind is EvidenceKind.FAA_EVENT for k in events))
        self.assertTrue(all(r.snapshot_id == fs.SNAPSHOT_ID for r in evidence.values()))
        self.assertTrue(all(r.data_mode == "live" for r in evidence.values()))
        self.assertEqual(evidence[fs.FLIGHT_BATCH].fetched_at_utc, self.at)

    async def test_missing_records_are_unknown_not_errors(self) -> None:
        del self.store.tables["agent_findings"][fs.WEATHER_ID]
        packet = await self.load()
        self.assertIsNone(packet.weather)
        del self.store.tables["flow_candidates"][fs.CANDIDATE_ID]
        packet = await self.load()
        self.assertIsNone(packet.candidate)
        self.assertIs(validate_packet(packet).verdict, ValidationVerdict.INSUFFICIENT_DATA)

    async def test_projection_from_another_snapshot_fails_validation(self) -> None:
        row = self.store.tables["projection_results"][fs.PROJECTION_ID]
        row["snapshot_id"] = "TEST-other-snapshot"
        packet = await self.load()
        self.assertEqual(packet.evidence[fs.PROJECTION_ID].snapshot_id, OUTSIDE_SNAPSHOT)
        self.assertIs(validate_packet(packet).verdict, ValidationVerdict.REJECT)

    async def test_tampered_stored_projection_is_unavailable(self) -> None:
        row = self.store.tables["projection_results"][fs.PROJECTION_ID]
        content = json.loads(row["content_json"])
        content["result"]["proposal"]["metrics"]["peak_runway_queue"] += 1
        row["content_json"] = json.dumps(content)
        with self.assertRaises(SpacetimeError):
            await self.load()

    async def test_unlisted_evidence_is_not_given_the_case_snapshot(self) -> None:
        row = self.store.tables["evidence_snapshots"][fs.SNAPSHOT_ID]
        snapshot = json.loads(row["content_json"])
        snapshot["evidence_ids"].remove(fs.ADSB_BATCH)
        row["content_json"] = json.dumps(snapshot)
        packet = await self.load()
        self.assertNotIn(fs.ADSB_BATCH, packet.evidence)
        outcome = validate_packet(packet)
        self.assertIs(outcome.verdict, ValidationVerdict.INSUFFICIENT_DATA)
        self.assertIn("evidence_missing", {f.code for f in outcome.findings})
