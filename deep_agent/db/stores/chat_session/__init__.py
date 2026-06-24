from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.chat_session.base import ChatSessionStoreBase
from deep_agent.db.stores.chat_session.mysql import MySQLChatSessionStore
from deep_agent.db.stores.chat_session.postgres import PostgresChatSessionStore
from deep_agent.db.stores.chat_session.sqlite import SqliteChatSessionStore
from deep_agent.db.stores.message.base import MessageStoreBase

__all__ = ["ChatSessionStoreBase", "create_chat_session_store"]


def create_chat_session_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
    *,
    message_store: MessageStoreBase | None = None,
) -> ChatSessionStoreBase:
    match dialect:
        case DbDialect.MYSQL:
            return MySQLChatSessionStore(session_factory, message_store=message_store)
        case DbDialect.SQLITE:
            return SqliteChatSessionStore(session_factory, message_store=message_store)
        case _:
            return PostgresChatSessionStore(session_factory, message_store=message_store)
