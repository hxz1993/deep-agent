from __future__ import annotations

from dataclasses import dataclass, field

from deep_agent.agent.provider_registry import ProviderRuntimeConfig
from deep_agent.db.models import Agent, LlmModel, LlmProvider

DEFAULT_INSTRUCTIONS = (
    "You are deep-agent, a helpful general-purpose assistant. "
    "Use the available tools when they help you answer accurately. "
    "Be concise and explain your reasoning when it is useful."
)
_SUPPORTED_INPUT_MODALITIES = {"text", "image", "audio", "video"}


def _input_modalities(llm_model: LlmModel) -> list[str]:
    """Ignore retired modality values that may remain in historical rows."""
    return [
        value
        for value in (llm_model.input_modalities or [])
        if value in _SUPPORTED_INPUT_MODALITIES
    ]


def _provider_config(
    llm_model: LlmModel,
    provider: LlmProvider | None,
) -> ProviderRuntimeConfig | None:
    provider_row = provider or getattr(llm_model, "provider", None)
    if provider_row is not None:
        return ProviderRuntimeConfig.from_row(provider_row)
    # Raw/legacy specs without a persisted provider remain supported for the
    # explicit `model=` API and non-runtime history projections. Runtime paths
    # load and pass the persisted provider explicitly before a request starts.
    return None


@dataclass
class AgentSpec:
    """Declarative description of an agent.

    ``capabilities=None`` uses default capabilities and no extension UI tools;
    otherwise the list contains product tool-package codes such as
    ``builtin:general`` or ``ui:deepcube``.
    """

    name: str
    model: str
    instructions: str
    capabilities: list[str] | None = None
    skills: list[str] = field(default_factory=list)
    base_url: str | None = None
    api_key: str | None = None
    temperature: float | None = None
    agent_id: str | None = None
    input_modalities: list[str] | None = None
    context_window_tokens: int | None = None
    max_output_tokens: int | None = None
    tags: list[str] | None = None
    llm_model_id: str | None = None
    provider_config: ProviderRuntimeConfig | None = None

    @classmethod
    def from_row(
        cls,
        row: Agent,
        llm_model: LlmModel,
        *,
        provider: LlmProvider | None = None,
    ) -> AgentSpec:
        return cls(
            name=row.name,
            model=llm_model.model,
            instructions=row.prompt,
            capabilities=row.capabilities,
            skills=list(row.skills or []),
            temperature=row.temperature,
            agent_id=row.id,
            input_modalities=_input_modalities(llm_model),
            context_window_tokens=llm_model.context_window_tokens,
            max_output_tokens=llm_model.max_output_tokens,
            tags=llm_model.tags,
            llm_model_id=llm_model.id,
            provider_config=_provider_config(llm_model, provider),
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
        skills: list[str] | None = None,
        provider: LlmProvider | None = None,
    ) -> AgentSpec:
        return cls(
            name=name,
            model=llm_model.model,
            instructions=instructions or DEFAULT_INSTRUCTIONS,
            capabilities=capabilities,
            skills=list(skills or []),
            temperature=temperature,
            input_modalities=_input_modalities(llm_model),
            context_window_tokens=llm_model.context_window_tokens,
            max_output_tokens=llm_model.max_output_tokens,
            tags=llm_model.tags,
            llm_model_id=llm_model.id,
            provider_config=_provider_config(llm_model, provider),
        )


def anonymous_spec() -> AgentSpec:
    """Minimal spec for UI/history paths that have no agent or LLM preset."""
    return AgentSpec(name="anonymous", model="", instructions="")
