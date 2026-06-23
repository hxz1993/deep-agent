from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.capabilities.ui.constants import SOURCE_PRIORITY
from deep_agent.db.models import UiToolDefinition
from deep_agent.db.repositories.ui_tool import UiToolRepository


async def sync_mcp_tools(
    db: AsyncSession,
    mcp_server_name: str,
    tools: list[UiToolDefinition],
    *,
    repo: UiToolRepository | None = None,
) -> tuple[int, int]:
    """Replace MCP-sourced tools for a server. Returns (upserted, removed)."""
    ui_tool_repo = repo or UiToolRepository()
    existing = await ui_tool_repo.list_all(db, source="mcp", mcp_server_name=mcp_server_name)
    new_names = {row.name for row in tools}

    removed = 0
    for row in existing:
        if row.name not in new_names:
            await db.delete(row)
            removed += 1

    upserted = 0
    for tool in tools:
        current = await ui_tool_repo.get_by_name(db, tool.name)
        if current is not None and SOURCE_PRIORITY[current.source] < SOURCE_PRIORITY["mcp"]:
            continue
        if current is not None and current.source == "manual" and current.override:
            continue
        if current is None:
            db.add(tool)
            upserted += 1
        else:
            current.description = tool.description
            current.parameters_json_schema = tool.parameters_json_schema
            current.widget = tool.widget
            current.result_schema = tool.result_schema
            current.metadata_ = tool.metadata_
            current.source = "mcp"
            current.mcp_server_name = mcp_server_name
            current.discovered_at = tool.discovered_at
            if not current.override:
                current.enabled = tool.enabled
            upserted += 1

    await db.flush()
    return upserted, removed
