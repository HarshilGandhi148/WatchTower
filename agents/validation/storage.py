"""Retrieval/receipt hooks for the validator; domain approval remains its callback."""

import json

from projection.aggregate_flow import FlowSnapshot, ProjectionComparison, compare
from projection.assumptions import (
    CapacityBasis,
    CapacityScenario,
    ProjectionAssumptions,
    ResponseBounds,
    ResponseParameters,
)
from shared.orchestration import AssessmentRequest, CandidateReady
from shared.persistence import ready_from_dict, storage_json, utc
from shared.spacetimedb_adapter import SpacetimeAdapter, SpacetimeError


class ValidationStorage:
    def __init__(self, adapter: SpacetimeAdapter) -> None:
        self.adapter = adapter

    async def register(self, request: AssessmentRequest) -> None:
        await self.adapter.register_assessment(request)

    async def retrieve(self, ready: CandidateReady) -> None:
        row = await self.adapter.load_record(
            "pending_deliveries", "candidate_id", ready.candidate_id
        )
        if ready_from_dict(json.loads(row["content_json"])) != ready:
            raise SpacetimeError("notification differs from saved candidate")
        candidate = await self.adapter.load_candidate(ready.candidate_id)
        snapshot = await self.adapter.load_snapshot(ready.envelope.snapshot_id)
        finding = await self.adapter.load_record(
            "agent_findings", "finding_id", ready.weather_assessment_id
        )
        if (
            candidate.envelope.snapshot_id != ready.envelope.snapshot_id
            or finding["snapshot_id"] != ready.envelope.snapshot_id
        ):
            raise SpacetimeError("candidate evidence does not match snapshot")
        for evidence_id in snapshot["evidence_ids"]:
            await self.adapter.load_record("evidence_records", "evidence_id", evidence_id)
        if candidate.projection_id is not None:
            await self.projection(candidate.projection_id)

    async def report(self, ready: CandidateReady) -> None:
        from datetime import UTC, datetime

        from agents.validation.reports import ReportValidator

        await ReportValidator(self).report(ready, datetime.now(UTC))

    async def receipt(self, ready: CandidateReady) -> None:
        await self.adapter.receipt(ready)

    async def check_open(self, snapshot_id: str) -> None:
        row = await self.adapter.load_record("evidence_snapshots", "snapshot_id", snapshot_id)
        if row["closed"]:
            raise SpacetimeError("case closed after restart")

    async def restart(self) -> None:
        await self.adapter.call("close_unfinished_cases")

    async def projection(self, projection_id: str) -> ProjectionComparison:
        """Recompute stored model inputs before any domain validation can use a result."""
        return (await self.projection_record(projection_id))[3]

    async def projection_record(
        self, projection_id: str
    ) -> tuple[FlowSnapshot, ProjectionAssumptions, ResponseParameters, ProjectionComparison]:
        """Stored inputs and their verified recomputation; a differing result is rejected."""
        v = await self.adapter.load_projection(projection_id)
        i, a = v["input"], v["assumptions"]
        flow = FlowSnapshot(
            i["snapshot_id"],
            utc(i["as_of_utc"]),
            i["initial_upstream_waiting"],
            i["initial_runway_queue"],
            tuple(i["demand_per_minute"]),
            i["demand_basis"],
        )
        capacity = CapacityScenario(
            a["capacity"]["name"],
            a["capacity"]["effective_capacity_per_hour"],
            CapacityBasis(a["capacity"]["basis"]),
        )
        assumptions = ProjectionAssumptions(
            capacity,
            a["horizon_minutes"],
            tuple(a["transit_delay_weights"]),
            ResponseBounds(**a["bounds"]),
            a["model_version"],
        )
        response = ResponseParameters(**v["response"])
        result = compare(flow, assumptions, response)
        if json.loads(storage_json(result)) != v["result"]:
            raise SpacetimeError("projection differs from deterministic computation")
        return flow, assumptions, response, result
