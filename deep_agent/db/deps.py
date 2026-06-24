from __future__ import annotations

from deep_agent.core.context import AppContextDep
from deep_agent.db.stores.agent import AgentStoreBase
from deep_agent.db.stores.mcp import McpServerStoreBase
from deep_agent.db.stores.ui_tool import UiToolStoreBase


def get_agent_store(ctx: AppContextDep) -> AgentStoreBase:
    return ctx.stores.agent


def get_mcp_store(ctx: AppContextDep) -> McpServerStoreBase:
    return ctx.stores.mcp


def get_ui_tool_store(ctx: AppContextDep) -> UiToolStoreBase:
    return ctx.stores.ui_tool
