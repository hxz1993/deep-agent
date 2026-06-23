from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.message.base import MessageStoreBase
from deep_agent.db.stores.session_query.base import (
    MAX_LIST_LIMIT,
    SessionListFilters,
    SessionQueryStoreBase,
    SessionSummaryRow,
    SortField,
)
from deep_agent.db.stores.session_query.mysql import MySQLSessionQueryStore
from deep_agent.db.stores.session_query.postgres import PostgresSessionQueryStore

__all__ = [
    "MAX_LIST_LIMIT",
    "SessionListFilters",
    "SessionQueryStoreBase",
    "SessionSummaryRow",
    "SortField",
    "create_session_query_store",
]


def create_session_query_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
    message_store: MessageStoreBase,
) -> SessionQueryStoreBase:
    match dialect:
        case DbDialect.MYSQL:
            return MySQLSessionQueryStore(session_factory, message_store)
        case _:
            return PostgresSessionQueryStore(session_factory, message_store)
