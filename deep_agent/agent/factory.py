from __future__ import annotations

from typing import Any

import httpx
from loguru import logger
from pydantic_ai import Agent
from pydantic_ai.models import DEFAULT_HTTP_TIMEOUT, Model, get_user_agent, parse_model_id
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.google import GoogleModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers import infer_provider_class
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.deepseek import DeepSeekProvider
from pydantic_ai.providers.google import GoogleProvider
from pydantic_ai.providers.openai import OpenAIProvider

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.models import DeepSeekChatModel
from deep_agent.agent.models.deepseek_thinking_retry import DeepSeekThinkingRetry
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.registry import CapabilityRegistry
from deep_agent.options import OPTION

_OPENAI_PREFIXES = {"openai", "openai-chat"}
_ANTHROPIC_PREFIXES = {"anthropic"}


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


def _resolve_anthropic(spec: AgentSpec, model_name: str) -> AnthropicModel:
    provider = AnthropicProvider(
        api_key=spec.api_key or None,
        base_url=_normalize_url(spec.base_url),
    )
    return AnthropicModel(model_name, provider=provider)


def _gemini_http_client(proxy: str) -> httpx.AsyncClient:
    # ponytail: trust_env=False so global HTTP_PROXY does not leak into Gemini-only traffic
    return httpx.AsyncClient(
        proxy=proxy,
        trust_env=False,
        timeout=httpx.Timeout(timeout=DEFAULT_HTTP_TIMEOUT, connect=5),
        headers={"User-Agent": get_user_agent()},
    )


def _resolve_google(spec: AgentSpec, model_name: str) -> GoogleModel:
    proxy = OPTION.agent.gemini_proxy or None
    provider = GoogleProvider(
        api_key=spec.api_key or None,
        base_url=_normalize_url(spec.base_url),
        http_client=_gemini_http_client(proxy) if proxy else None,
    )
    return GoogleModel(model_name, provider=provider)


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
        parsed_provider, model_name = parse_model_id(spec.model)
        if parsed_provider == "google":
            return _resolve_google(spec, model_name)
        if spec.base_url:
            return _resolve_openai_compatible(spec, spec.model)
        return spec.model

    model_name = _model_name_without_prefix(spec.model)
    if provider_name in _OPENAI_PREFIXES:
        if not spec.api_key and not spec.base_url:
            return spec.model
        return _resolve_openai_compatible(spec, model_name)
    if provider_name in _ANTHROPIC_PREFIXES:
        if not spec.api_key and not spec.base_url:
            return spec.model
        return _resolve_anthropic(spec, model_name)

    try:
        provider_class = infer_provider_class(provider_name)
    except Exception:
        if spec.base_url:
            return _resolve_openai_compatible(spec, model_name)
        raise

    if provider_class is GoogleProvider:
        return _resolve_google(spec, model_name)

    if provider_class is DeepSeekProvider:
        provider = DeepSeekProvider(api_key=spec.api_key)
        if (
            not spec.base_url
            or _normalize_url(spec.base_url) == _normalize_url(provider.base_url)
        ):
            profile = DeepSeekProvider.model_profile(model_name)
            logger.debug("Resolving {} with DeepSeekChatModel", spec.model)
            return DeepSeekChatModel(model_name, provider=provider, profile=profile)

    if spec.base_url:
        return _resolve_openai_compatible(spec, model_name)

    return spec.model


def _toolset_id(toolset: Any) -> str | None:
    toolset_id = getattr(toolset, "id", None)
    if toolset_id is not None:
        return str(toolset_id)
    wrapped = getattr(toolset, "wrapped", None)
    if wrapped is not None:
        return _toolset_id(wrapped)
    return None


def _toolsets_for_spec(
    registry: CapabilityRegistry,
    spec: AgentSpec,
    *,
    mcp_toolsets: list[Any] | None = None,
    dynamic_ui_toolset: Any | None = None,
    mcp_resource_toolset: Any | None = None,
) -> list[Any]:
    """Resolve capability toolsets and include UI tools for HITL."""
    toolsets = list(registry.toolsets(spec.capabilities))
    if mcp_toolsets:
        toolsets.extend(mcp_toolsets)
    if mcp_resource_toolset is not None:
        toolsets.append(mcp_resource_toolset)

    if dynamic_ui_toolset is not None:
        toolsets = [toolset for toolset in toolsets if _toolset_id(toolset) != "ui"]
        toolsets.append(dynamic_ui_toolset)
        return toolsets

    if spec.capabilities is not None and "ui" not in spec.capabilities and "ui" in registry.names():
        toolsets.append(registry.get("ui").toolset)
    return toolsets


def build_agent(
    spec: AgentSpec,
    registry: CapabilityRegistry,
    *,
    request_retries: int = 2,
    mcp_toolsets: list[Any] | None = None,
    dynamic_ui_toolset: Any | None = None,
    mcp_resource_toolset: Any | None = None,
) -> Agent[AgentDeps, str]:
    """Construct a Pydantic AI agent from a spec + the capability registry.

    The model string (e.g. "openai:gpt-4o") selects the provider, so switching
    providers needs only a config change. Set ``base_url``/``api_key`` on the
    spec to target an OpenAI-compatible endpoint. Capability instructions ride
    along with their toolsets and are injected automatically when enabled.
    """
    model = _resolve_model(spec)
    toolsets = _toolsets_for_spec(
        registry,
        spec,
        mcp_toolsets=mcp_toolsets,
        dynamic_ui_toolset=dynamic_ui_toolset,
        mcp_resource_toolset=mcp_resource_toolset,
    )
    capabilities = [DeepSeekThinkingRetry()] if isinstance(model, DeepSeekChatModel) else None
    return Agent(
        model,
        deps_type=AgentDeps,
        instructions=spec.instructions,
        toolsets=toolsets,
        capabilities=capabilities,
        retries=request_retries,
    )
