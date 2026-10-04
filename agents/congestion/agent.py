"""Congestion uAgent factory: thin wiring between uAgents and CongestionService.

runtime/bureau.py composes the three agents (docs/orchestration.md); this module only
prepares the congestion identity and its handlers. It never starts a server or registers
an endpoint itself: the Bureau or a later distributed deployment decides that.

Protocol (validation owns orchestration):
  cycle          -> CaseOpenedMessage to validation
  validation     -> AssessmentRequestMessage (round 1/2) -> weather request to weather
  weather        -> WeatherAssessmentReplyMessage
  advance        -> CandidateReadyMessage to validation (references only)

Every inbound message is accepted only from the configured peer address; the role
declared inside a message is never treated as authentication.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from uagents import Agent, Context

from agents.congestion.persistence import PersistentCongestionService
from agents.congestion.projections import ProjectionSettings
from agents.congestion.service import CongestionService, ProtocolError
from shared.messages import (
    AssessmentRequestMessage,
    CandidateReadyMessage,
    CaseOpenedMessage,
    EnvelopeModel,
    WeatherAssessmentReplyMessage,
    WeatherAssessmentRequestMessage,
)
from shared.orchestration import CandidateReady
from shared.settings import AirportConfig, SpacetimeSettings
from shared.spacetimedb_adapter import SpacetimeAdapter, SpacetimeError

AGENT_NAME = "congestion"
ADVANCE_SECONDS = 2.0
PROJECTION_SETTINGS = (
    "DEPARTURE_CAPACITY_PER_HOUR",
    "DEPARTURE_BACKLOG_UPSTREAM",
    "DEPARTURE_BACKLOG_RUNWAY_QUEUE",
)

Handler = Callable[..., Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AgentConfig:
    seed: str
    weather_address: str
    validation_address: str
    database: SpacetimeSettings
    airport: AirportConfig = AirportConfig()
    cycle_seconds: float = 60.0  # plan: assessment at most once per minute per case
    projection: ProjectionSettings | None = None  # None = projections disabled

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AgentConfig:
        """Read names from .env.example. Raises ValueError for anything missing."""
        missing = [
            name
            for name in (
                "CONGESTION_AGENT_SEED",
                "WEATHER_AGENT_ADDRESS",
                "VALIDATION_AGENT_ADDRESS",
            )
            if not env.get(name, "").strip()
        ]
        if missing:
            raise ValueError(f"missing configuration: {', '.join(missing)}")
        airport = AirportConfig.from_env(env)
        return cls(
            seed=env["CONGESTION_AGENT_SEED"].strip(),
            weather_address=env["WEATHER_AGENT_ADDRESS"].strip(),
            validation_address=env["VALIDATION_AGENT_ADDRESS"].strip(),
            database=SpacetimeSettings.from_env(env, role="congestion"),
            airport=airport,
            projection=_projection_settings(env),
        )


def _projection_settings(env: Mapping[str, str]) -> ProjectionSettings | None:
    """Capacity enables projections; the backlog pair is an optional operator fallback."""
    values = {name: env.get(name, "").strip() for name in PROJECTION_SETTINGS}
    capacity, upstream, queue = (values[name] for name in PROJECTION_SETTINGS)
    if not capacity:
        if upstream or queue:
            raise ValueError("backlog settings need DEPARTURE_CAPACITY_PER_HOUR")
        return None
    if bool(upstream) != bool(queue):
        raise ValueError(
            "DEPARTURE_BACKLOG_UPSTREAM and DEPARTURE_BACKLOG_RUNWAY_QUEUE go together"
        )
    try:
        return ProjectionSettings(
            int(capacity),
            int(upstream) if upstream else None,
            int(queue) if queue else None,
        )
    except ValueError as error:
        raise ValueError(f"invalid projection settings: {error}") from error


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _candidate_message(candidate: CandidateReady) -> CandidateReadyMessage:
    return CandidateReadyMessage(
        envelope=EnvelopeModel.from_envelope(candidate.envelope),
        candidate_id=candidate.candidate_id,
        weather_assessment_id=candidate.weather_assessment_id,
        assessment_round=candidate.assessment_round,
        projection_id=candidate.projection_id,
    )


def build_agent(
    config: AgentConfig,
    *,
    service: PersistentCongestionService | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Agent:
    """Prepare the congestion agent for runtime/bureau.py (does not start it)."""
    agent = Agent(
        name=AGENT_NAME,
        seed=config.seed,
        loop=loop,
        enable_agent_inspector=False,
        publish_agent_details=False,
        report_events=False,
    )
    svc = service or PersistentCongestionService(
        SpacetimeAdapter(config.database),
        configuration=config.airport,
        service=CongestionService(projection=config.projection),
    )

    async def cycle(ctx: Context) -> None:
        try:
            opening = await svc.cycle(_utcnow())
        except (SpacetimeError, ValueError):
            ctx.logger.warning("Stored snapshot unavailable; cycle remains retryable")
            return
        await ctx.send(
            config.validation_address,
            CaseOpenedMessage(envelope=EnvelopeModel.from_envelope(opening)),
        )

    async def on_assessment_request(
        ctx: Context, sender: str, msg: AssessmentRequestMessage
    ) -> None:
        if sender != config.validation_address:
            ctx.logger.warning("ignored assessment request from an unexpected sender")
            return
        try:
            weather = await svc.assessment(msg.to_request(), _utcnow())
        except (ProtocolError, ValueError) as error:
            ctx.logger.warning(f"dropped assessment request: {error}")
            return
        await ctx.send(
            config.weather_address, WeatherAssessmentRequestMessage.from_request(weather)
        )

    async def on_weather_reply(
        ctx: Context, sender: str, msg: WeatherAssessmentReplyMessage
    ) -> None:
        if sender != config.weather_address:
            ctx.logger.warning("ignored weather reply from an unexpected sender")
            return
        try:
            envelope, status, assessment_id = msg.to_reply()
            await svc.weather(envelope, status, assessment_id, _utcnow())
        except (ProtocolError, ValueError) as error:
            ctx.logger.warning(f"dropped weather reply: {error}")
            return
        ctx.logger.info("Weather reply processed")

    async def advance(ctx: Context) -> None:
        async def send(candidate: CandidateReady) -> None:
            await ctx.send(config.validation_address, _candidate_message(candidate))

        try:
            await svc.advance(_utcnow(), send)
        except (SpacetimeError, ValueError):
            ctx.logger.warning("Candidate persistence unavailable; retry remains pending")

    _register(agent.on_interval(period=config.cycle_seconds), cycle)
    _register(agent.on_interval(period=ADVANCE_SECONDS), advance)
    # No `replies=`: uAgents expects declared replies to go back to the sender, but the
    # answer to validation (CandidateReady) is sent later, after the weather exchange.
    _register(agent.on_message(model=AssessmentRequestMessage), on_assessment_request)
    _register(agent.on_message(model=WeatherAssessmentReplyMessage), on_weather_reply)
    return agent


def _register(decorator: Callable[[Handler], object], handler: Handler) -> None:
    """Apply a uAgents decorator by call, keeping the handler's own strict typing."""
    decorator(handler)
