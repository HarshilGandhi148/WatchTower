"""Read-only weather chat on the existing weather identity, with no case writes."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

from agents.weather.assessment import DEFAULT_RULES, WeatherRules, assess_weather
from agents.weather.llm import Explainer
from shared.contracts import (
    AgentName,
    MessageEnvelope,
    WeatherAssessmentRequest,
    WeatherSnapshot,
    weather_utc,
)

UNAVAILABLE = "KDTW weather evidence is unavailable. Current conditions are unknown."
SCOPE = (
    "I report KDTW weather from retained METAR and TAF evidence. Ask about weather, "
    "wind, visibility, ceiling or the forecast. I cannot issue aircraft instructions, "
    "select runways or determine official capacity."
)


class WeatherChat:
    """Integration injects a retained snapshot reader; never use a fixture fallback.

    A single shared one-minute cache bounds public traffic and model use. Questions
    select this fixed report only; their text is never sent to the model or stored.
    """

    def __init__(
        self,
        load_snapshot: Callable[[], Awaitable[WeatherSnapshot]],
        *,
        explainer: Explainer | None = None,
        rules: WeatherRules = DEFAULT_RULES,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._load, self._explainer = load_snapshot, explainer or Explainer()
        self._rules, self._clock = rules, clock
        self._lock = asyncio.Lock()
        self._until: datetime | None = None
        self._answer = UNAVAILABLE

    async def answer(self, question: str) -> str:
        if len(question) > 2000 or not re.search(
            r"\b(weather|metar|taf|wind|winds|gusts?|visibility|ceiling|forecast|snow|rain|"
            r"temperature|thunderstorms?|clouds?)\b",
            question,
            re.IGNORECASE,
        ):
            return SCOPE
        async with self._lock:
            now = self._clock()
            weather_utc(now)
            if self._until is not None and now < self._until:
                return self._answer
            self._answer = UNAVAILABLE
            try:
                async with asyncio.timeout(12):
                    snap = await self._load()
                    now = self._clock()
                    weather_utc(now)
                    if snap.cutoff_utc > now:
                        raise ValueError("Future snapshot")
                    identity = str(uuid4())
                    envelope = MessageEnvelope(
                        schema_version=1,
                        message_id=identity,
                        case_id="chat-" + identity,
                        sender_agent=AgentName.CONGESTION,
                        created_at_utc=now,
                        snapshot_id=snap.snapshot_id,
                        evidence_ids=snap.evidence_ids,
                        airport_icao="KDTW",
                        valid_until_utc=now + timedelta(seconds=30),
                        correlation_id=identity,
                    )
                    # Internal pure-function input only; never send this as a peer request.
                    finding = assess_weather(
                        WeatherAssessmentRequest(envelope, now, now + timedelta(minutes=15), 1),
                        snap,
                        now,
                        self._rules,
                    )
                    finding = await self._explainer.explain(finding)
                    self._answer = (
                        f"KDTW weather, assessed {now.isoformat()} for the next 15 minutes. "
                        f"Status: {finding.status.value}; quality: {finding.quality.value}. "
                        f"{finding.summary} "
                        "Coverage: METAR/TAF only; regional and runway surface hazards are "
                        "not assessed. Project thresholds are not official capacity rules."
                    )
            except Exception:
                # Do not expose source text, credentials, headers or I/O exception messages.
                self._answer = UNAVAILABLE
            self._until = self._clock() + timedelta(seconds=60)
            return self._answer


def attach_chat(agent: Agent, chat: WeatherChat) -> None:
    """Register signed chat handlers without constructing another agent or starting it."""
    protocol = Protocol(spec=chat_protocol_spec)

    @protocol.on_message(ChatMessage)
    async def handle_message(ctx: Context, sender: str, msg: ChatMessage) -> None:
        try:
            await ctx.send(
                sender,
                ChatAcknowledgement(timestamp=datetime.now(UTC), acknowledged_msg_id=msg.msg_id),
            )
            chunks = [item.text for item in msg.content if isinstance(item, TextContent)]
            question = " ".join(chunks) if sum(map(len, chunks)) <= 2000 else ""
            response = await chat.answer(question)
            await ctx.send(
                sender,
                ChatMessage(
                    timestamp=datetime.now(UTC),
                    msg_id=uuid4(),
                    content=[
                        TextContent(type="text", text=response),
                        EndSessionContent(type="end-session"),
                    ],
                ),
            )
        except Exception:
            ctx.logger.warning("Weather chat delivery unavailable")

    @protocol.on_message(ChatAcknowledgement)
    async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement) -> None:
        pass

    agent.include(protocol, publish_manifest=False)
