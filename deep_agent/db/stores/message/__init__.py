from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.message.base import EARLY_MESSAGE_LIMIT, MessageStoreBase
from deep_agent.db.stores.message.mysql import MySQLMessageStore
from deep_agent.db.stores.message.postgres import PostgresMessageStore

__all__ = ["EARLY_MESSAGE_LIMIT", "MessageStoreBase", "create_message_store"]


def create_message_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> MessageStoreBase:
    match dialect:
        case DbDialect.MYSQL:
            return MySQLMessageStore(session_factory)
        case _:
            return PostgresMessageStore(session_factory)
