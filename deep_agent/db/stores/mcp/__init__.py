from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.mcp.base import McpServerStoreBase
from deep_agent.db.stores.mcp.mysql import MySQLMcpServerStore
from deep_agent.db.stores.mcp.postgres import PostgresMcpServerStore
from deep_agent.db.stores.mcp.sqlite import SqliteMcpServerStore

__all__ = ["McpServerStoreBase", "create_mcp_store"]


def create_mcp_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> McpServerStoreBase:
    match dialect:
        case DbDialect.MYSQL:
            return MySQLMcpServerStore(session_factory)
        case DbDialect.SQLITE:
            return SqliteMcpServerStore(session_factory)
        case _:
            return PostgresMcpServerStore(session_factory)
