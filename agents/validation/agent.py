"""Validation uAgent factory for the three-agent Bureau; it never acts as a controller.

Wiring only. The partner-built gateway (protocol.py) routes cases and stores receipts.
``validate`` loads the stored packet (loader.py) and runs the pure checks
(service.validate_packet). ``persist`` records the outcome in the advisory ledger. A
read-only ASI:One chat (chat.py) answers questions from validated advisories.

The database has no advisory table yet, so published advisories live in this process's
ledger and the log. This factory never starts a server, registers an endpoint, approves
an advisory or writes observations or decisions. Put the returned agent in
``runtime.bureau.build_bureau`` (not ``build_coordinated_bureau``, which would register
orchestration a second time).

Run alone, ``python -m agents.validation.agent`` from the repository root starts only
this agent: its mailbox, Agent inspector link and ASI:One chat. Only
VALIDATION_AGENT_SEED is required. Without the peer addresses or database settings, the
agent runs chat-only and logs what is missing. With them, coordination waits for
congestion and weather to send cases.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime

from uagents import Agent

from agents.validation import lifecycle
from agents.validation.chat import ChatResponder, build_chat_protocol
from agents.validation.ledger import AdvisoryLedger
from agents.validation.loader import load_packet
from agents.validation.orchestrator import CaseRecord, Orchestrator
from agents.validation.packet import ValidationConfig
from agents.validation.protocol import register_orchestration
from agents.validation.service import ValidationOutcome, validate_packet
from agents.validation.storage import ValidationStorage
from shared.contracts import AgentName
from shared.llm import LlmSettings, Transport, http_transport
from shared.orchestration import CandidateReady, CasePhase, ValidationVerdict
from shared.settings import SpacetimeSettings
from shared.spacetimedb_adapter import SpacetimeAdapter

LOGGER = logging.getLogger("watchtower.validation")
AGENT_NAME = "watchtower-validation"  # public; changing it does not change the address
REQUIRED = ("VALIDATION_AGENT_SEED", "WEATHER_AGENT_ADDRESS", "CONGESTION_AGENT_ADDRESS")
_PHASE_VERDICT = {
    CasePhase.REJECTED: ValidationVerdict.REJECT,
    CasePhase.INSUFFICIENT_DATA: ValidationVerdict.INSUFFICIENT_DATA,
    CasePhase.READY_TO_PUBLISH: ValidationVerdict.PUBLISH,
}


@dataclass(frozen=True, slots=True)
class AgentConfig:
    seed: str = field(repr=False)
    # Peers and database are needed for case coordination. Without them the agent runs
    # chat-only (standalone setup: inspector, mailbox and ASI:One chat).
    weather_address: str | None = None
    congestion_address: str | None = None
    database: SpacetimeSettings | None = None
    llm: LlmSettings = field(default_factory=LlmSettings)
    validation: ValidationConfig = field(default_factory=ValidationConfig)
    mailbox: bool = True  # lets ASI:One reach the chat; connect it once via the inspector
    port: int = 8002  # local server port when run alone; the Bureau uses its own

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> AgentConfig:
        """Read names from .env.example. Errors name variables, never their values."""
        missing = [name for name in REQUIRED if not env.get(name, "").strip()]
        if missing:
            raise ValueError(f"missing configuration: {', '.join(missing)}")
        return cls(
            seed=env["VALIDATION_AGENT_SEED"].strip(),
            weather_address=env["WEATHER_AGENT_ADDRESS"].strip(),
            congestion_address=env["CONGESTION_AGENT_ADDRESS"].strip(),
            database=SpacetimeSettings.from_env(env, role="validation"),
            llm=LlmSettings.from_env(env),
            port=_port(env),
        )

    @classmethod
    def standalone_from_env(cls, env: Mapping[str, str]) -> AgentConfig:
        """Only the seed is required; missing peers or database turn coordination off."""
        seed = env.get("VALIDATION_AGENT_SEED", "").strip()
        if not seed:
            raise ValueError("missing configuration: VALIDATION_AGENT_SEED")
        weather = env.get("WEATHER_AGENT_ADDRESS", "").strip() or None
        congestion = env.get("CONGESTION_AGENT_ADDRESS", "").strip() or None
        try:
            database: SpacetimeSettings | None = SpacetimeSettings.from_env(env, role="validation")
        except ValueError:
            database = None
        return cls(
            seed=seed,
            weather_address=weather,
            congestion_address=congestion,
            database=database,
            llm=LlmSettings.from_env(env),
            port=_port(env),
        )

    def coordination_gaps(self) -> tuple[str, ...]:
        """What is missing for case coordination; empty means it can be registered."""
        gaps = []
        if self.weather_address is None:
            gaps.append("WEATHER_AGENT_ADDRESS")
        if self.congestion_address is None:
            gaps.append("CONGESTION_AGENT_ADDRESS")
        if self.database is None:
            gaps.append("SpacetimeDB settings (SPACETIMEDB_HTTP_URL, _DATABASE, _VALIDATION_TOKEN)")
        return tuple(gaps)


def _port(env: Mapping[str, str]) -> int:
    text = env.get("VALIDATION_AGENT_PORT", "").strip()
    if not text:
        return 8002
    if not text.isdigit() or not 1 <= int(text) <= 65535:
        raise ValueError("VALIDATION_AGENT_PORT must be a port number")
    return int(text)


class ValidationCallbacks:
    """The gateway's validate/persist callbacks, sharing one ledger."""

    def __init__(
        self,
        coordinator: Orchestrator,
        adapter: SpacetimeAdapter,
        storage: ValidationStorage,
        ledger: AdvisoryLedger,
        config: ValidationConfig,
        clock: Callable[[], datetime],
        log: Callable[[str], None],
    ) -> None:
        self.coordinator = coordinator
        self.adapter = adapter
        self.storage = storage
        self.ledger = ledger
        self.config = config
        self.clock = clock
        self.log = log
        self._outcomes: dict[str, ValidationOutcome] = {}

    async def validate(self, ready: CandidateReady) -> ValidationVerdict:
        case_id = ready.envelope.case_id
        opening = self.coordinator.get_case(case_id).opening
        packet = await load_packet(self.adapter, self.storage, opening, ready, self.clock())
        previous = None
        if packet.candidate is not None:
            advisory_id = lifecycle.advisory_id(packet.candidate)
            previous = self.ledger.previous(advisory_id, packet.now_utc)
        outcome = validate_packet(packet, self.config, previous)
        self._outcomes[case_id] = outcome
        return outcome.verdict

    async def persist(self, case: CaseRecord) -> None:
        case_id = case.opening.case_id
        outcome = self._outcomes.get(case_id)
        advisory = outcome.advisory if outcome is not None else None
        if case.phase is CasePhase.READY_TO_PUBLISH and advisory is not None:
            if self.ledger.publish(advisory):
                self.log(
                    f"advisory {advisory.advisory_id} v{advisory.version} published for "
                    f"review ({advisory.priority.value}, {advisory.quality.value})"
                )
        else:
            codes = tuple(f.code for f in outcome.findings) if outcome is not None else ()
            verdict = _PHASE_VERDICT.get(case.phase, ValidationVerdict.INSUFFICIENT_DATA)
            if verdict is ValidationVerdict.PUBLISH:  # published phase without an advisory
                verdict = ValidationVerdict.INSUFFICIENT_DATA
            reason = case.reason or case.phase.value
            if self.ledger.record_result(case_id, verdict, reason, codes, self.clock()):
                self.log(f"case {case_id} not published: {reason}")
        self._outcomes.pop(case_id, None)


def build_agent(
    config: AgentConfig,
    *,
    adapter: SpacetimeAdapter | None = None,
    llm_transport: Transport = http_transport,
    loop: asyncio.AbstractEventLoop | None = None,
    clock: Callable[[], datetime] | None = None,
) -> Agent:
    """Prepare the validation agent for runtime/bureau.py (does not start it)."""
    agent = Agent(
        name=AGENT_NAME,
        seed=config.seed,
        port=config.port,
        loop=loop,
        mailbox=config.mailbox,
        publish_agent_details=config.mailbox,
    )
    ledger = AdvisoryLedger()
    gaps = config.coordination_gaps()
    if gaps:
        LOGGER.warning("Case coordination off (chat only); missing: %s", ", ".join(gaps))
    else:
        assert config.weather_address and config.congestion_address and config.database
        coordinator = Orchestrator(
            {
                AgentName.WEATHER: config.weather_address,
                AgentName.CONGESTION: config.congestion_address,
                AgentName.VALIDATION: agent.address,
            }
        )
        store = adapter or SpacetimeAdapter(config.database)
        storage = ValidationStorage(store)
        callbacks = ValidationCallbacks(
            coordinator,
            store,
            storage,
            ledger,
            config.validation,
            clock or (lambda: datetime.now(UTC)),
            LOGGER.info,
        )
        register_orchestration(
            agent,
            coordinator,
            validate=callbacks.validate,
            persist=callbacks.persist,
            storage=storage,
        )
    responder = ChatResponder(ledger, config.llm, transport=llm_transport)
    agent.include(build_chat_protocol(responder, clock), publish_manifest=True)
    return agent


def main() -> None:
    """Start only the validator (mailbox, inspector, chat); values come from the environment."""
    logging.basicConfig(level=logging.INFO)
    try:
        config = AgentConfig.standalone_from_env(os.environ)
    except ValueError as error:
        raise SystemExit(f"Validation agent not started: {error}") from None
    agent = build_agent(config)
    LOGGER.info("Validation agent address: %s", agent.address)
    agent.run()  # type: ignore[no-untyped-call]  # the pinned SDK leaves run() unannotated


if __name__ == "__main__":
    main()
