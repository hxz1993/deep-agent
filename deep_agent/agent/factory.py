from __future__ import annotations

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.registry import CapabilityRegistry

_OPENAI_PREFIXES = {"openai", "openai-chat"}


def _resolve_model(spec: AgentSpec) -> str | Model:
    """Turn the spec's model string into a model for Pydantic AI.

    Without ``base_url`` we pass the plain string (e.g. "openai:gpt-4o") and let
    Pydantic AI pick the provider from the prefix. With ``base_url`` set we route
    through an explicit OpenAI-compatible provider, which works for OpenAI as
    well as most domestic models (DeepSeek, Qwen, GLM, Kimi, ...).
    """
    if not spec.base_url:
        return spec.model

    # A custom base_url already fixes the provider to an OpenAI-compatible
    # endpoint, so drop a leading "openai:"/"openai-chat:" prefix if present.
    prefix, sep, rest = spec.model.partition(":")
    model_name = rest if sep and prefix in _OPENAI_PREFIXES else spec.model

    provider = OpenAIProvider(base_url=spec.base_url, api_key=spec.api_key)
    return OpenAIChatModel(model_name, provider=provider)


def build_agent(spec: AgentSpec, registry: CapabilityRegistry) -> Agent[AgentDeps, str]:
    """Construct a Pydantic AI agent from a spec + the capability registry.

    The model string (e.g. "openai:gpt-4o") selects the provider, so switching
    providers needs only a config change. Set ``base_url``/``api_key`` on the
    spec to target an OpenAI-compatible endpoint. Capability instructions ride
    along with their toolsets and are injected automatically when enabled.
    """
    toolsets = registry.toolsets(spec.capabilities)
    return Agent(
        _resolve_model(spec),
        deps_type=AgentDeps,
        instructions=spec.instructions,
        toolsets=toolsets,
        retries=2,
    )
