from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.ui_tool.base import UiToolStoreBase
from deep_agent.db.stores.ui_tool.mysql import MySQLUiToolStore
from deep_agent.db.stores.ui_tool.postgres import PostgresUiToolStore

__all__ = ["UiToolStoreBase", "create_ui_tool_store"]


def create_ui_tool_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> UiToolStoreBase:
    match dialect:
        case DbDialect.MYSQL:
            return MySQLUiToolStore(session_factory)
        case _:
            return PostgresUiToolStore(session_factory)
