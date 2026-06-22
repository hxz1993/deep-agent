from __future__ import annotations

from loguru import logger
from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers import infer_provider_class
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.openai import OpenAIProvider

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.models import DeepSeekChatModel
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.registry import CapabilityRegistry

_OPENAI_PREFIXES = {"openai", "openai-chat"}


def _model_provider_prefix(model: str) -> str | None:
    prefix, sep, _rest = model.partition(":")
    return prefix if sep else None


def _model_name_without_prefix(model: str) -> str:
    _prefix, sep, rest = model.partition(":")
    return rest if sep else model


def _normalize_url(url: str | None) -> str | None:
    if url is None:
        return None
    stripped = url.strip()
    if not stripped:
        return None
    return stripped.rstrip("/")


def _resolve_openai_compatible(spec: AgentSpec, model_name: str) -> Model:
    provider = OpenAIProvider(base_url=spec.base_url, api_key=spec.api_key)
    return OpenAIChatModel(model_name, provider=provider)


def _resolve_model(spec: AgentSpec) -> str | Model:
    """Turn the spec's model string into a model for Pydantic AI.

    For ``provider:model`` strings, the provider class comes from
    :func:`~pydantic_ai.providers.infer_provider_class`. When that class is
    :class:`~pydantic_ai.providers.deepseek.DeepSeekProvider` and ``base_url`` is
    unset or matches the provider's official endpoint, the native provider is used
    so reasoning fields round-trip correctly. A different ``base_url`` falls back
    to :class:`~pydantic_ai.providers.openai.OpenAIProvider`.
    """
    provider_name = _model_provider_prefix(spec.model)
    if provider_name is None:
        if spec.base_url:
            return _resolve_openai_compatible(spec, spec.model)
        return spec.model

    provider_class = infer_provider_class(provider_name)

    if provider_class is DeepSeekProvider:
        provider = DeepSeekProvider(api_key=spec.api_key)
        if (
            not spec.base_url
            or _normalize_url(spec.base_url) == _normalize_url(provider.base_url)
        ):
            model_name = _model_name_without_prefix(spec.model)
            profile = DeepSeekProvider.model_profile(model_name)
            logger.debug("Resolving {} with DeepSeekChatModel", spec.model)
            return DeepSeekChatModel(model_name, provider=provider, profile=profile)

    if spec.base_url:
        prefix, sep, rest = spec.model.partition(":")
        model_name = rest if sep and prefix in _OPENAI_PREFIXES else _model_name_without_prefix(spec.model)
        return _resolve_openai_compatible(spec, model_name)

    return spec.model


def build_agent(
    spec: AgentSpec,
    registry: CapabilityRegistry,
    *,
    request_retries: int = 2,
) -> Agent[AgentDeps, str]:
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
        retries=request_retries,
    )
