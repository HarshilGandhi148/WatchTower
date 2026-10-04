"""Assemble the three supplied uAgent identities without adding an orchestrator identity."""

from collections.abc import Mapping

from uagents import Agent, Bureau

from agents.validation.orchestrator import Orchestrator
from agents.validation.protocol import PersistOutcome, ValidateCandidate, register_orchestration
from agents.validation.storage import ValidationStorage
from shared.contracts import AgentName


def build_bureau(
    agents: Mapping[AgentName, Agent], *, port: int = 8000, endpoint: str | None = None
) -> Bureau:
    """Run prepared specialists and the validation coordinator in one Bureau.

    Agent factories and provider/database callbacks are supplied by their owners.
    This helper deliberately does not create seedless agents or start a server.
    """
    if set(agents) != set(AgentName):
        raise ValueError("Exactly weather, congestion and validation agents are required")
    if len({agent.address for agent in agents.values()}) != 3:
        raise ValueError("Each agent needs a distinct identity")
    if type(port) is not int or not 1 <= port <= 65535:
        raise ValueError("Bureau port must be between 1 and 65535")
    return Bureau(
        agents=[agents[role] for role in AgentName],
        port=port,
        endpoint=endpoint,
    )


def build_coordinated_bureau(
    agents: Mapping[AgentName, Agent],
    *,
    validate: ValidateCandidate,
    persist: PersistOutcome,
    port: int = 8000,
    endpoint: str | None = None,
    storage: ValidationStorage | None = None,
) -> Bureau:
    """Wire orchestration into validation before running the same three identities."""
    bureau = build_bureau(agents, port=port, endpoint=endpoint)
    coordinator = Orchestrator({role: agent.address for role, agent in agents.items()})
    register_orchestration(
        agents[AgentName.VALIDATION],
        coordinator,
        validate=validate,
        persist=persist,
        storage=storage,
    )
    return bureau
