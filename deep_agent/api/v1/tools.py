from __future__ import annotations

import itertools

from fastapi import APIRouter

from deep_agent.capabilities.mcp.resolver import describe_mcp_capabilities
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.registry import load_selectable_ui_tools
from deep_agent.core.context import AppContextDep
from deep_agent.schemas.chat import CapabilityDescription, ToolDescription

router = APIRouter()


@router.get("/tools", response_model=list[CapabilityDescription])
async def list_capabilities(ctx: AppContextDep) -> list[CapabilityDescription]:
    """List all registered capabilities (native / skill / mcp / ui) and their tools."""
    mcp_rows = await ctx.stores.mcp.list_all()
    mcp_caps = await describe_mcp_capabilities(ctx.stores, mcp_rows=mcp_rows)
    caps = [
        CapabilityDescription(**cap) for cap in
        itertools.chain(mcp_caps, ctx.registry.describe())
    ]

    for tool in builtin_ui_records():
        caps.append(
            CapabilityDescription(
                name=f"ui-builtin:{tool.name}",
                kind="ui-client",
                source="builtin",
                description=tool.description,
                default_enabled=True,
                locked=True,
                tools=[
                    ToolDescription(
                        name=tool.name,
                        description=tool.description,
                    )
                ],
            )
        )

    for tool in await load_selectable_ui_tools(ctx.stores):
        caps.append(
            CapabilityDescription(
                name=f"ui:{tool.name}",
                kind="ui-client",
                source=tool.source,
                description=tool.description,
                tools=[
                    ToolDescription(
                        name=tool.name,
                        description=tool.description,
                    )
                ],
            )
        )
    return caps
