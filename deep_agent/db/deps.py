from __future__ import annotations

from fastapi import HTTPException, status

from deep_agent.core.context import AppContext, AppContextDep
from deep_agent.db.stores.agent import AgentStoreBase
from deep_agent.db.stores.bundle import DataStores
from deep_agent.db.stores.mcp import McpServerStoreBase
from deep_agent.db.stores.ui_tool import UiToolStoreBase


def require_db(stores: DataStores) -> None:
    if stores.agent is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Database is not configured. "
                "Set DEEP_AGENT_DBMS_NAME/USER/PASSWORD/HOSTNAME/PORT/DATABASE."
            ),
        )


def get_stores(ctx: AppContextDep) -> DataStores:
    return ctx.stores


def get_agent_store(ctx: AppContextDep) -> AgentStoreBase:
    require_db(ctx.stores)
    assert ctx.stores.agent is not None
    return ctx.stores.agent


def get_mcp_store(ctx: AppContextDep) -> McpServerStoreBase:
    require_db(ctx.stores)
    assert ctx.stores.mcp is not None
    return ctx.stores.mcp


def get_ui_tool_store(ctx: AppContextDep) -> UiToolStoreBase:
    require_db(ctx.stores)
    assert ctx.stores.ui_tool is not None
    return ctx.stores.ui_tool
