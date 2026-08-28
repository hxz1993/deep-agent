from __future__ import annotations

from typing import Any

import httpx2
from pydantic_ai import Agent
from pydantic_ai.capabilities import AbstractCapability, IncludeToolReturnSchemas, ToolSearch
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
from deep_agent.agent.planning import build_planning_capability
from deep_agent.agent.provider_registry import resolve_provider_model
from deep_agent.agent.spec import AgentSpec
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.tool_discovery import (
    MCP_SEARCH_RESULT_LIMIT,
    TOOL_SEARCH_DESCRIPTION,
    TOOL_SEARCH_PARAMETER_DESCRIPTION,
    build_return_schema_selector,
    build_mcp_discovery_toolset,
    search_deferred_tools,
)
from deep_agent.capabilities.registry import ToolPackage, ToolPackageRegistry
from deep_agent.code_mode import FilePayloadResolvingToolset, HybridCodeMode
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


def _resolve_official_deepseek(spec: AgentSpec, model_name: str) -> OpenAIChatModel | None:
    """Use the native profile when a catalog row points at DeepSeek's API."""
    provider = DeepSeekProvider(api_key=spec.api_key)
    if _normalize_url(spec.base_url) == _normalize_url(provider.base_url):
        return OpenAIChatModel(model_name, provider=provider)
    return None


def _resolve_anthropic(spec: AgentSpec, model_name: str) -> AnthropicModel:
    provider = AnthropicProvider(
        api_key=spec.api_key or None,
        base_url=_normalize_url(spec.base_url),
    )
    return AnthropicModel(model_name, provider=provider)


def _gemini_http_client(proxy: str) -> httpx2.AsyncClient:
    # ponytail: trust_env=False so global HTTP_PROXY does not leak into Gemini-only traffic
    return httpx2.AsyncClient(
        proxy=proxy,
        trust_env=False,
        timeout=httpx2.Timeout(timeout=DEFAULT_HTTP_TIMEOUT, connect=5),
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

    Persisted models always use their explicit provider configuration. The
    legacy raw ``model=`` API keeps Pydantic AI's prefix resolution behavior,
    but it is never used to infer the provider of a database model.

    For legacy ``provider:model`` strings, the provider class comes from
    :func:`~pydantic_ai.providers.infer_provider_class`. When that class is
    :class:`~pydantic_ai.providers.deepseek.DeepSeekProvider` and ``base_url`` is
    unset or matches the provider's official endpoint, the native provider is used
    so reasoning fields round-trip correctly. A different ``base_url`` falls back
    to :class:`~pydantic_ai.providers.openai.OpenAIProvider`.
    """
    if spec.provider_config is not None:
        return resolve_provider_model(
            model_name=spec.model,
            provider=spec.provider_config,
            input_modalities=spec.input_modalities,
            tags=spec.tags,
        )

    provider_name = _model_provider_prefix(spec.model)
    if provider_name is None:
        parsed_provider, model_name = parse_model_id(spec.model)
        if parsed_provider == "google":
            return _resolve_google(spec, model_name)
        if spec.base_url:
            deepseek_model = _resolve_official_deepseek(spec, spec.model)
            if deepseek_model is not None:
                return deepseek_model
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
        if not spec.base_url:
            return OpenAIChatModel(
                model_name,
                provider=DeepSeekProvider(api_key=spec.api_key),
            )
        deepseek_model = _resolve_official_deepseek(spec, model_name)
        if deepseek_model is not None:
            return deepseek_model

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
    file_toolset: Any | None = None,
    file_export_toolset: Any | None = None,
    skill_resource_toolset: Any | None = None,
    skill_script_toolset: Any | None = None,
    history_toolset: Any | None = None,
    mcp_server_names: list[str] | None = None,
    estimator: TokenEstimator | None = None,
) -> list[Any]:
    """Resolve selected product tool packages into Pydantic AI toolsets."""
    toolsets: list[Any] = []
    for package in registry.selected(spec.capabilities):
        toolsets.append(package.toolset)
    if mcp_toolsets:
        effective_mcp_toolsets = mcp_toolsets
        if "builtin:code-mode" in spec.capabilities:
            effective_mcp_toolsets = [
                FilePayloadResolvingToolset(toolset)
                for toolset in mcp_toolsets
            ]
        discovery = build_mcp_discovery_toolset(
            effective_mcp_toolsets,
            server_names=mcp_server_names or (),
            estimator=estimator or TokenEstimator.conservative(),
        )
        if discovery is not None:
            toolsets.append(discovery)
    if mcp_resource_toolset is not None:
        toolsets.append(mcp_resource_toolset)
    if file_toolset is not None:
        toolsets.append(file_toolset)
    if file_export_toolset is not None:
        toolsets.append(file_export_toolset)
    if skill_resource_toolset is not None:
        toolsets.append(skill_resource_toolset)
    if skill_script_toolset is not None:
        toolsets.append(skill_script_toolset)
    if history_toolset is not None:
        toolsets.append(history_toolset)

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
    file_toolset: Any | None = None,
    file_export_toolset: Any | None = None,
    skill_resource_toolset: Any | None = None,
    skill_script_toolset: Any | None = None,
    history_toolset: Any | None = None,
    skill_capability: AbstractCapability[AgentDeps] | None = None,
    context_compaction: AbstractCapability[AgentDeps] | None = None,
    tool_output_limits: AbstractCapability[AgentDeps] | None = None,
    mcp_server_names: list[str] | None = None,
    estimator: TokenEstimator | None = None,
) -> Agent[AgentDeps, str]:
    """Construct a Pydantic AI agent from a spec + the tool package registry.

    Persisted models use the explicit Provider configuration carried by the
    spec. The legacy raw-model path remains only for callers that do not resolve
    a database model. Tool-package instructions ride along with their toolsets
    and are injected automatically when enabled.
    """
    model = _resolve_model(spec)
    toolsets = _toolsets_for_spec(
        registry,
        spec,
        mcp_toolsets=mcp_toolsets,
        dynamic_ui_packages=dynamic_ui_packages,
        mcp_resource_toolset=mcp_resource_toolset,
        file_toolset=file_toolset,
        file_export_toolset=file_export_toolset,
        skill_resource_toolset=skill_resource_toolset,
        skill_script_toolset=skill_script_toolset,
        history_toolset=history_toolset,
        mcp_server_names=mcp_server_names,
        estimator=estimator,
    )
    capabilities: list[AbstractCapability[AgentDeps]] = []
    if context_compaction is not None:
        capabilities.append(context_compaction)
    if skill_capability is not None:
        capabilities.append(skill_capability)
    if tool_output_limits is not None:
        capabilities.append(tool_output_limits)
    if mcp_toolsets:
        capabilities.append(
            ToolSearch(
                strategy=search_deferred_tools,
                max_results=MCP_SEARCH_RESULT_LIMIT,
                tool_description=TOOL_SEARCH_DESCRIPTION,
                parameter_description=TOOL_SEARCH_PARAMETER_DESCRIPTION,
            )
        )
        capabilities.append(
            IncludeToolReturnSchemas(
                tools=build_return_schema_selector(
                    server_names=mcp_server_names or (),
                )
            )
        )
    if "builtin:code-mode" in spec.capabilities:
        capabilities.append(
            HybridCodeMode(
                tools=[],
                dynamic_catalog=True,
                max_tool_calls=100,
                resource_limits={
                    "max_duration_secs": 30,
                    "max_memory": 256 * 1024 * 1024,
                },
            )
        )
    capabilities.append(build_planning_capability())
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
