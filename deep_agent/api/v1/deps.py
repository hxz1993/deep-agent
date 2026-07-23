from __future__ import annotations

from dataclasses import replace

from pydantic_ai.settings import ModelSettings
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.factory import build_agent
from deep_agent.agent.input_capabilities import augment_instructions_with_capabilities
from deep_agent.agent.limits import resolve_agent_max_requests
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.spec import DEFAULT_INSTRUCTIONS, AgentSpec
from deep_agent.capabilities.mcp.resolver import load_mcp_rows, resolve_mcp_toolsets
from deep_agent.capabilities.mcp.resources import build_mcp_resource_toolset
from deep_agent.capabilities.ui.registry import prepare_ui_toolset_for_spec
from deep_agent.core.context import AppContext
from deep_agent.core.exceptions import AgentNotFoundError, LlmModelNotFoundError
from deep_agent.options import OPTION


def _model_settings_for_spec(spec: AgentSpec) -> ModelSettings | None:
    if spec.temperature is None:
        return None
    return ModelSettings(temperature=spec.temperature)


async def build_runner(
    ctx: AppContext,
    *,
    model: str | None = None,
    model_id: str | None = None,
    temperature: float | None = None,
    capabilities: list[str] | None = None,
    request_headers: dict[str, str] | None = None,
) -> AgentRunner:
    """Assemble a per-request AgentRunner from settings + registry + store."""
    if model_id is not None:
        llm_row = await ctx.stores.llm_model.get_by_id(model_id)
        if llm_row is None:
            raise LlmModelNotFoundError(model_id)
        spec = AgentSpec.from_llm_model(llm_row, temperature=temperature)
    elif model:
        spec = AgentSpec(
            name="default",
            model=model,
            instructions=DEFAULT_INSTRUCTIONS,
            temperature=temperature,
        )
    else:
        llm_rows = await ctx.stores.llm_model.list_all()
        if not llm_rows:
            raise LlmModelNotFoundError("default")
        spec = AgentSpec.from_llm_model(llm_rows[0], temperature=temperature)
    if capabilities is not None:
        spec.capabilities = capabilities
    return await _build_runner_from_spec(spec, ctx, request_headers=request_headers)


async def build_runner_for_agent(
    ctx: AppContext,
    agent_id: str,
    *,
    model_id: str | None = None,
    request_headers: dict[str, str] | None = None,
) -> AgentRunner:
    """Assemble a per-request AgentRunner from a database agent configuration."""
    row = await ctx.stores.agent.get_by_id(agent_id)
    if row is None:
        raise AgentNotFoundError(agent_id)

    effective_model_id = model_id or row.model_id
    llm_row = await ctx.stores.llm_model.get_by_id(effective_model_id)
    if llm_row is None:
        raise LlmModelNotFoundError(effective_model_id)

    spec = AgentSpec.from_row(row, llm_row)
    return await _build_runner_from_spec(spec, ctx, request_headers=request_headers)


async def _build_runner_from_spec(
    spec: AgentSpec,
    ctx: AppContext,
    *,
    request_headers: dict[str, str] | None = None,
) -> AgentRunner:
    mcp_rows = await load_mcp_rows(ctx.stores)

    dynamic_ui_toolset, ui_tool_hints = await prepare_ui_toolset_for_spec(
        ctx.stores,
        spec,
    )

    mcp_toolsets = await resolve_mcp_toolsets(ctx.stores, spec, mcp_rows=mcp_rows)
    mcp_resource_toolset = build_mcp_resource_toolset(spec, mcp_rows)

    spec = replace(spec, instructions=augment_instructions_with_capabilities(spec))
    agent = build_agent(
        spec,
        ctx.registry,
        request_retries=OPTION.agent.request_retries,
        mcp_toolsets=mcp_toolsets,
        dynamic_ui_toolset=dynamic_ui_toolset,
        mcp_resource_toolset=mcp_resource_toolset,
    )
    limits = UsageLimits(
        request_limit=resolve_agent_max_requests(spec.max_requests, OPTION.agent.max_requests),
        total_tokens_limit=OPTION.agent.max_total_tokens,
    )
    return AgentRunner(
        agent,
        ctx.store,
        limits,
        ui_tool_hints=ui_tool_hints,
        model_settings=_model_settings_for_spec(spec),
        input_modalities=spec.input_modalities,
        capabilities_source=spec.capabilities_source,
        model_base_url=spec.base_url,
    )
