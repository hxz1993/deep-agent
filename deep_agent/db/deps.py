from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.core.context import AppContext, AppContextDep
from deep_agent.db.repositories.agent import AgentRepository
from deep_agent.db.repositories.mcp import McpServerRepository
from deep_agent.db.repositories.session import SessionRepository
from deep_agent.db.repositories.ui_tool import UiToolRepository

_agent_repo = AgentRepository()
_mcp_repo = McpServerRepository()
_session_repo = SessionRepository()
_ui_tool_repo = UiToolRepository()


@asynccontextmanager
async def db_session(ctx: AppContext) -> AsyncIterator[AsyncSession]:
    session_factory = ctx.db_session_factory
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=(
                "Database is not configured. "
                "Set DEEP_AGENT_DBMS_NAME/USER/PASSWORD/HOSTNAME/PORT/DATABASE."
            ),
        )
    async with session_factory() as session:
        yield session


async def get_db(ctx: AppContextDep) -> AsyncIterator[AsyncSession]:
    async with db_session(ctx) as session:
        yield session


async def get_uow(ctx: AppContextDep) -> AsyncIterator[AsyncSession]:
    from deep_agent.db.unit_of_work import unit_of_work

    async with unit_of_work(ctx) as session:
        yield session


def get_agent_repo() -> AgentRepository:
    return _agent_repo


def get_mcp_repo() -> McpServerRepository:
    return _mcp_repo


def get_session_repo() -> SessionRepository:
    return _session_repo


def get_ui_tool_repo() -> UiToolRepository:
    return _ui_tool_repo
