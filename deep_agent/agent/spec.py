from __future__ import annotations

from dataclasses import dataclass

from deep_agent.db.models import Agent
from deep_agent.options import OPTION


@dataclass
class AgentSpec:
    """Declarative description of an agent.

    ``capabilities=None`` means "all registered capabilities"; otherwise it is a
    list of capability names (e.g. ["native", "skill:calculator"]) to enable.
    """

    name: str
    model: str
    instructions: str
    capabilities: list[str] | None = None
    # Optional OpenAI-compatible endpoint override (see OPTION.agent.model_base_url).
    base_url: str | None = None
    api_key: str | None = None
    max_requests: int | None = None
    agent_id: str | None = None

    @classmethod
    def from_row(cls, row: Agent) -> AgentSpec:
        return cls(
            name=row.name,
            model=row.model,
            instructions=row.prompt,
            capabilities=row.capabilities,
            base_url=row.base_url,
            api_key=row.api_key,
            max_requests=row.max_requests,
            agent_id=row.id,
        )


def default_spec() -> AgentSpec:
    return AgentSpec(
        name="default",
        model=OPTION.agent.model,
        instructions=OPTION.agent.system_prompt,
        capabilities=None,
        base_url=OPTION.agent.model_base_url or None,
        api_key=OPTION.agent.model_api_key or None,
    )
