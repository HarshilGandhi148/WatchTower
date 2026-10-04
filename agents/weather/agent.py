"""Prepare the weather uAgent; runtime owns startup and integration callbacks."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass, field

from uagents import Agent, Context

from agents.weather.assessment import DEFAULT_RULES, WeatherRules
from agents.weather.chat import WeatherChat, attach_chat
from agents.weather.llm import AsiConfig, AsiModel, Explainer
from agents.weather.service import WeatherDependencies, WeatherService
from agents.weather.triggers import ChangeDetector, TriggerDependencies
from shared.messages import WeatherAssessmentReplyMessage, WeatherAssessmentRequestMessage


@dataclass(frozen=True, slots=True)
class AgentConfig:
    seed: str = field(repr=False)
    congestion_address: str
    validation_address: str
    asi: AsiConfig | None = None
    mailbox: bool = False
    port: int = 8001

    def __post_init__(self) -> None:
        if any(
            not value.strip()
            for value in (self.seed, self.congestion_address, self.validation_address)
        ):
            raise ValueError("Weather seed and peer addresses are required")
        if self.congestion_address == self.validation_address:
            raise ValueError("Weather peers must have distinct identities")
        if not 1 <= self.port <= 65535:
            raise ValueError("Invalid weather agent port")

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AgentConfig:
        names = ("WEATHER_AGENT_SEED", "CONGESTION_AGENT_ADDRESS", "VALIDATION_AGENT_ADDRESS")
        missing = [name for name in names if not env.get(name, "").strip()]
        if missing:
            raise ValueError("Missing configuration: " + ", ".join(missing))
        if env.get("AIRPORT_ICAO", "KDTW") != "KDTW" or env.get("APP_MODE", "live") != "live":
            raise ValueError("Weather factory supports live KDTW configuration only")
        return cls(
            env[names[0]].strip(),
            env[names[1]].strip(),
            env[names[2]].strip(),
            asi=AsiConfig.from_env(env),
        )


def build_agent(
    config: AgentConfig,
    dependencies: WeatherDependencies,
    *,
    rules: WeatherRules = DEFAULT_RULES,
    service: WeatherService | None = None,
    triggers: TriggerDependencies | None = None,
    chat: WeatherChat | None = None,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Agent:
    agent = Agent(
        name="weather",
        seed=config.seed,
        loop=loop,
        enable_agent_inspector=False,
        publish_agent_details=False,
        report_events=False,
        mailbox=config.mailbox,
        port=config.port,
    )
    svc = service or WeatherService(
        dependencies, rules=rules, explainer=Explainer(AsiModel(config.asi) if config.asi else None)
    )

    async def request_handler(
        ctx: Context, sender: str, msg: WeatherAssessmentRequestMessage
    ) -> None:
        if sender != config.congestion_address:
            ctx.logger.warning("Ignored weather request from unexpected sender")
            return
        try:
            reply = await svc.handle(msg.to_request())
            await ctx.send(config.congestion_address, reply)
        except Exception:
            # Never log source text, secrets or exception messages from injected I/O.
            ctx.logger.warning("Weather request failed; unchanged retry may replay the result")

    agent.on_message(model=WeatherAssessmentRequestMessage, replies=WeatherAssessmentReplyMessage)(
        request_handler
    )

    async def shutdown(ctx: Context) -> None:
        await svc.close()

    agent.on_event("shutdown")(shutdown)
    if triggers is not None:
        detector = ChangeDetector(triggers, rules=rules)

        async def changed(ctx: Context) -> None:
            try:
                await detector.poll()
            except Exception:
                ctx.logger.warning("Weather change check unavailable; no case published")

        agent.on_interval(period=60.0)(changed)
    if chat is not None:
        attach_chat(agent, chat)
    return agent


if __name__ == "__main__":
    from agents.weather.launch import main

    main()
