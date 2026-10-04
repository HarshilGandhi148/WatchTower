"""Durable congestion workflow and outbox; all async transitions share one lock."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from uuid import uuid4

from agents.congestion.feeds import stored_feeds
from agents.congestion.service import CongestionService
from shared.contracts import MessageEnvelope, WeatherAssessmentRequest
from shared.orchestration import AssessmentRequest, CandidateReady
from shared.settings import AirportConfig
from shared.spacetimedb_adapter import SpacetimeAdapter


class PersistentCongestionService:
    def __init__(
        self,
        adapter: SpacetimeAdapter,
        *,
        configuration: AirportConfig | None = None,
        service: CongestionService | None = None,
    ) -> None:
        self.adapter = adapter
        self.configuration = configuration or AirportConfig()
        self.service = service or CongestionService()
        self._lock = asyncio.Lock()
        self._initialized = False
        self._opening: MessageEnvelope | None = None
        self._snapshot_request: tuple[str, str, datetime] | None = None

    async def initialize(self) -> None:
        if not self._initialized:
            # Saved notifications survive; incomplete assessments cannot resume from
            # volatile specialist state or accept late messages after restart.
            await self.adapter.call("close_unfinished_cases")
            self._initialized = True

    async def cycle(self, now: datetime) -> MessageEnvelope:
        async with self._lock:
            await self.initialize()
            if self._opening is not None and self._opening.valid_until_utc > now:
                return self._opening
            if (
                self._snapshot_request is None
                or self._snapshot_request[2] + timedelta(seconds=120) <= now
            ):
                key = uuid4().hex
                self._snapshot_request = (f"snap-{key}", f"case-{key}", now)
            snapshot_id, case_id, start = self._snapshot_request
            snapshot = await self.adapter.create_snapshot(
                snapshot_id,
                case_id,
                self.configuration,
                start,
                start + timedelta(minutes=15),
                start + timedelta(seconds=120),
            )
            self._opening = self.service.start_cycle(stored_feeds(snapshot), now)
            self._snapshot_request = None
            return self._opening

    async def assessment(
        self, request: AssessmentRequest, now: datetime
    ) -> WeatherAssessmentRequest:
        async with self._lock:
            return self.service.on_assessment_request(request, now)

    async def weather(
        self, envelope: MessageEnvelope, status: str, assessment_id: str | None, now: datetime
    ) -> None:
        async with self._lock:
            self.service.on_weather_reply(envelope, status, assessment_id, now)

    async def advance(
        self, now: datetime, send: Callable[[CandidateReady], Awaitable[None]]
    ) -> None:
        async with self._lock:
            await self.initialize()
            step = self.service.advance(now)
            for ready in step.ready:
                await self.adapter.save_candidate(
                    self.service.candidates[ready.candidate_id], ready
                )
                self.service.mark_persisted(ready)
            deliveries = await self.adapter.pending_deliveries()
        # Notifications can repeat until a durable validator receipt. No provider or
        # assessment lock is held during network delivery.
        for ready in deliveries:
            if ready.envelope.valid_until_utc > now:
                try:
                    await send(ready)
                except Exception:
                    continue
