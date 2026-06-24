from __future__ import annotations

from pydantic_ai.usage import UsageLimits

from deep_agent.agent.factory import build_agent
from deep_agent.agent.limits import resolve_agent_max_requests
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.spec import AgentSpec, default_spec
from deep_agent.capabilities.mcp.resolver import load_mcp_rows, resolve_mcp_toolsets
from deep_agent.capabilities.ui.registry import prepare_ui_toolset_for_spec
from deep_agent.core.context import AppContext
from deep_agent.core.exceptions import AgentNotFoundError
from deep_agent.options import OPTION


async def build_runner(
    ctx: AppContext,
    *,
    model: str | None = None,
    capabilities: list[str] | None = None,
) -> AgentRunner:
    """Assemble a per-request AgentRunner from settings + registry + store."""
    spec = default_spec()
    if model:
        spec.model = model
    if capabilities is not None:
        spec.capabilities = capabilities
    return await _build_runner_from_spec(spec, ctx)


async def build_runner_for_agent(ctx: AppContext, agent_id: str) -> AgentRunner:
    """Assemble a per-request AgentRunner from a database agent configuration."""
    row = await ctx.stores.agent.get_by_id(agent_id)
    if row is None:
        raise AgentNotFoundError(agent_id)

    spec = AgentSpec.from_row(row)
    return await _build_runner_from_spec(spec, ctx)


async def _build_runner_from_spec(spec: AgentSpec, ctx: AppContext) -> AgentRunner:
    mcp_rows = await load_mcp_rows(ctx.stores)

    dynamic_ui_toolset, ui_tool_hints = await prepare_ui_toolset_for_spec(
        ctx.stores,
        spec,
        mcp_rows=mcp_rows,
    )

    mcp_toolsets = await resolve_mcp_toolsets(ctx.stores, spec, mcp_rows=mcp_rows)

    agent = build_agent(
        spec,
        ctx.registry,
        request_retries=OPTION.agent.request_retries,
        mcp_toolsets=mcp_toolsets,
        dynamic_ui_toolset=dynamic_ui_toolset,
    )
    limits = UsageLimits(
        request_limit=resolve_agent_max_requests(spec.max_requests, OPTION.agent.max_requests),
        total_tokens_limit=OPTION.agent.max_total_tokens,
    )
    return AgentRunner(agent, ctx.store, limits, ui_tool_hints=ui_tool_hints)
