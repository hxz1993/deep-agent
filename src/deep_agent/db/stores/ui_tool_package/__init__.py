from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.ui_tool_package.base import UiToolPackageStoreBase
from deep_agent.db.stores.ui_tool_package.mysql import MySQLUiToolPackageStore
from deep_agent.db.stores.ui_tool_package.postgres import PostgresUiToolPackageStore
from deep_agent.db.stores.ui_tool_package.sqlite import SqliteUiToolPackageStore

__all__ = ["UiToolPackageStoreBase", "create_ui_tool_package_store"]


def create_ui_tool_package_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> UiToolPackageStoreBase:
    match dialect:
        case DbDialect.MYSQL:
            return MySQLUiToolPackageStore(session_factory)
        case DbDialect.SQLITE:
            return SqliteUiToolPackageStore(session_factory)
        case _:
            return PostgresUiToolPackageStore(session_factory)
