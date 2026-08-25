from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.chat_session.base import ChatSessionStoreBase

__all__ = ["ChatSessionStoreBase", "create_chat_session_store"]


def create_chat_session_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> ChatSessionStoreBase:
    del dialect
    return ChatSessionStoreBase(session_factory)
