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

from agents.congestion.feeds import AeroApiFetcher, FeedConfig, FeedSnapshot, fetch_feeds
from agents.congestion.metrics import KDTW_REFERENCE_POINT
from agents.congestion.service import CongestionService, ProtocolError
from data.flightaware import AeroApiConfig
from projection.assumptions import CapacityBasis, CapacityScenario, ProjectionAssumptions
from shared.messages import (
    AssessmentRequestMessage,
    CandidateReadyMessage,
    CaseOpenedMessage,
    EnvelopeModel,
    WeatherAssessmentReplyMessage,
    WeatherAssessmentRequestMessage,
)
from shared.orchestration import CandidateReady

AGENT_NAME = "congestion"
ADVANCE_SECONDS = 2.0

Handler = Callable[..., Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AgentConfig:
    seed: str
    weather_address: str
    validation_address: str
    feeds: FeedConfig
    cycle_seconds: float = 60.0  # plan: assessment at most once per minute per case
    aeroapi: AeroApiConfig | None = None  # None = AeroAPI disabled
    departure_capacity_per_hour: int | None = None  # None = projections disabled

    def projection_assumptions(self) -> ProjectionAssumptions | None:
        """Operator-configured capacity enables projections; nothing is assumed otherwise."""
        if self.departure_capacity_per_hour is None:
            return None
        capacity = CapacityScenario(
            "operator-configured departure capacity",
            self.departure_capacity_per_hour,
            CapacityBasis.OPERATOR_CONFIGURED,
        )
        return ProjectionAssumptions(capacity=capacity)

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
        user_agent = env.get("DATA_USER_AGENT", "").strip()
        if not user_agent or "<" in user_agent:
            raise ValueError("DATA_USER_AGENT must identify the team (see .env.example)")
        return cls(
            seed=env["CONGESTION_AGENT_SEED"].strip(),
            weather_address=env["WEATHER_AGENT_ADDRESS"].strip(),
            validation_address=env["VALIDATION_AGENT_ADDRESS"].strip(),
            feeds=FeedConfig(
                adsb_base_url=env.get("ADSB_BASE_URL", "https://api.adsb.lol"),
                faa_url=env.get(
                    "FAA_STATUS_URL", "https://nasstatus.faa.gov/api/airport-status-information"
                ),
                user_agent=user_agent,
                center=KDTW_REFERENCE_POINT,
                radius_nm=int(env.get("TRAFFIC_RADIUS_NM", "40")),
            ),
            aeroapi=AeroApiConfig.from_env(env),
            departure_capacity_per_hour=_optional_int(env, "DEPARTURE_CAPACITY_PER_HOUR"),
        )


def _optional_int(env: Mapping[str, str], name: str) -> int | None:
    text = env.get(name, "").strip()
    if not text:
        return None
    try:
        return int(text)
    except ValueError as error:
        raise ValueError(f"{name} must be a whole number") from error


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
    service: CongestionService | None = None,
    fetch: Callable[[FeedConfig], FeedSnapshot] | None = None,
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
    svc = service or CongestionService(projection=config.projection_assumptions())
    if fetch is None:
        flights = (
            AeroApiFetcher(config.aeroapi, config.feeds.user_agent) if config.aeroapi else None
        )

        def fetch(feed_config: FeedConfig) -> FeedSnapshot:
            return fetch_feeds(feed_config, flights=flights)

    async def cycle(ctx: Context) -> None:
        feeds = await asyncio.to_thread(fetch, config.feeds)  # blocking I/O off the loop
        for error in feeds.errors:
            ctx.logger.warning(f"feed unavailable: {error}")
        opening = svc.start_cycle(feeds, _utcnow())
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
            weather = svc.on_assessment_request(msg.to_request(), _utcnow())
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
            result = svc.on_weather_reply(envelope, status, assessment_id, _utcnow())
        except (ProtocolError, ValueError) as error:
            ctx.logger.warning(f"dropped weather reply: {error}")
            return
        ctx.logger.info(f"weather reply {envelope.message_id}: {result.outcome}")

    async def advance(ctx: Context) -> None:
        step = svc.advance(_utcnow())
        for case_id in step.abandoned:
            ctx.logger.warning(f"{case_id}: no weather assessment to reference; not submitted")
        for candidate in step.ready:
            await ctx.send(config.validation_address, _candidate_message(candidate))
            ctx.logger.info(
                f"candidate {candidate.candidate_id} round {candidate.assessment_round} sent"
            )

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
