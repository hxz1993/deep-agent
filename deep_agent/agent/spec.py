from __future__ import annotations

from dataclasses import dataclass

from deep_agent.db.models import Agent, LlmModel

DEFAULT_INSTRUCTIONS = (
    "You are deep-agent, a helpful general-purpose assistant. "
    "Use the available tools when they help you answer accurately. "
    "Be concise and explain your reasoning when it is useful."
)


@dataclass
class AgentSpec:
    """Declarative description of an agent.

    ``capabilities=None`` uses default capabilities and no extension UI tools;
    otherwise the list may include entries such as ``ui:deep_cube__clarify``.
    """

    name: str
    model: str
    instructions: str
    capabilities: list[str] | None = None
    base_url: str | None = None
    api_key: str | None = None
    temperature: float | None = None
    max_requests: int | None = None
    agent_id: str | None = None
    input_modalities: list[str] | None = None
    capabilities_source: str | None = None

    @classmethod
    def from_row(cls, row: Agent, llm_model: LlmModel) -> AgentSpec:
        return cls(
            name=row.name,
            model=llm_model.model,
            instructions=row.prompt,
            capabilities=row.capabilities,
            base_url=llm_model.base_url,
            api_key=llm_model.api_key,
            temperature=row.temperature,
            max_requests=row.max_requests,
            agent_id=row.id,
            input_modalities=llm_model.input_modalities,
            capabilities_source=llm_model.capabilities_source,
        )

    @classmethod
    def from_llm_model(
        cls,
        llm_model: LlmModel,
        *,
        name: str = "default",
        instructions: str | None = None,
        temperature: float | None = None,
        capabilities: list[str] | None = None,
        max_requests: int | None = None,
    ) -> AgentSpec:
        return cls(
            name=name,
            model=llm_model.model,
            instructions=instructions or DEFAULT_INSTRUCTIONS,
            capabilities=capabilities,
            base_url=llm_model.base_url,
            api_key=llm_model.api_key,
            temperature=temperature,
            max_requests=max_requests,
            input_modalities=llm_model.input_modalities,
            capabilities_source=llm_model.capabilities_source,
        )


def anonymous_spec() -> AgentSpec:
    """Minimal spec for UI/history paths that have no agent or LLM preset."""
    return AgentSpec(name="anonymous", model="", instructions="")
