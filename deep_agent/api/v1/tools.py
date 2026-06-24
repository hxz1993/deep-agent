from __future__ import annotations

from fastapi import APIRouter

from deep_agent.capabilities.mcp.resolver import describe_mcp_capabilities
from deep_agent.capabilities.ui.registry import load_effective_ui_tools
from deep_agent.core.context import AppContextDep
from deep_agent.schemas.chat import CapabilityDescription, ToolDescription

router = APIRouter()


@router.get("/tools", response_model=list[CapabilityDescription])
async def list_capabilities(ctx: AppContextDep) -> list[CapabilityDescription]:
    """List all registered capabilities (native / skill / mcp / ui) and their tools."""
    caps = [CapabilityDescription(**cap) for cap in ctx.registry.describe()]
    caps.extend(CapabilityDescription(**cap) for cap in await describe_mcp_capabilities(ctx.stores))

    if ctx.stores.ui_tool is None:
        return caps

    effective = await load_effective_ui_tools(ctx.stores, spec=_anonymous_spec())
    dynamic_tools = [record for record in effective.definitions if record.source in {"mcp", "manual"}]

    if dynamic_tools:
        caps.append(
            CapabilityDescription(
                name="ui-client",
                kind="ui-client",
                description="Client-executed UI tools discovered via MCP or configured manually.",
                tools=[
                    ToolDescription(
                        name=tool.name,
                        description=tool.description,
                    )
                    for tool in dynamic_tools
                ],
            )
        )
    return caps


def _anonymous_spec():
    from deep_agent.agent.spec import default_spec

    return default_spec()
