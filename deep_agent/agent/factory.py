from __future__ import annotations

from typing import Any

import httpx
from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability
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
from deep_agent.agent.context_references import context_reference_instructions
from deep_agent.agent.models import OpenAICompatibleVideoChatModel
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.registry import ToolPackage, ToolPackageRegistry
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
    if "video" in (spec.input_modalities or []) or "video_url" in (spec.tags or []):
        return OpenAICompatibleVideoChatModel(model_name, provider=provider)
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


def resolve_model(spec: AgentSpec) -> str | Model:
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
            return OpenAIChatModel(model_name, provider=provider)

    if spec.base_url:
        return _resolve_openai_compatible(spec, model_name)

    return spec.model


# Backward-compatible internal seam used by the title path and existing tests.
# New code may import the public ``resolve_model`` name.
_resolve_model = resolve_model


def _toolsets_for_spec(
    registry: ToolPackageRegistry,
    spec: AgentSpec,
    *,
    mcp_toolsets: list[Any] | None = None,
    dynamic_ui_packages: list[ToolPackage] | None = None,
    mcp_resource_toolset: Any | None = None,
) -> list[Any]:
    """Resolve selected product tool packages into Pydantic AI toolsets."""
    toolsets = list(registry.toolsets(spec.capabilities))
    if mcp_toolsets:
        toolsets.extend(mcp_toolsets)
    if mcp_resource_toolset is not None:
        toolsets.append(mcp_resource_toolset)

    if dynamic_ui_packages:
        toolsets.extend(package.toolset for package in dynamic_ui_packages)
    return toolsets


def build_agent(
    spec: AgentSpec,
    registry: ToolPackageRegistry,
    *,
    request_retries: int = 2,
    mcp_toolsets: list[Any] | None = None,
    dynamic_ui_packages: list[ToolPackage] | None = None,
    mcp_resource_toolset: Any | None = None,
    context_compaction: AbstractCapability[AgentDeps] | None = None,
) -> Agent[AgentDeps, str]:
    """Construct a Pydantic AI agent from a spec + the tool package registry.

    The model string (e.g. "openai:gpt-4o") selects the provider, so switching
    providers needs only a config change. Set ``base_url``/``api_key`` on the
    spec to target an OpenAI-compatible endpoint. Tool-package instructions ride
    along with their toolsets and are injected automatically when enabled.
    """
    model = _resolve_model(spec)
    toolsets = _toolsets_for_spec(
        registry,
        spec,
        mcp_toolsets=mcp_toolsets,
        dynamic_ui_packages=dynamic_ui_packages,
        mcp_resource_toolset=mcp_resource_toolset,
    )
    capabilities: list[AbstractCapability[AgentDeps]] = []
    if context_compaction is not None:
        capabilities.append(context_compaction)
    instructions: list[Any] = []
    if spec.instructions:
        instructions.append(spec.instructions)
    instructions.append(context_reference_instructions)
    return Agent(
        model,
        deps_type=AgentDeps,
        instructions=instructions,
        toolsets=toolsets,
        capabilities=capabilities or None,
        retries=request_retries,
    )
