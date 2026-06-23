from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.agent.base import AgentStoreBase
from deep_agent.db.stores.agent.mysql import MySQLAgentStore
from deep_agent.db.stores.agent.postgres import PostgresAgentStore

__all__ = ["AgentStoreBase", "create_agent_store"]


def create_agent_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> AgentStoreBase:
    match dialect:
        case DbDialect.MYSQL:
            return MySQLAgentStore(session_factory)
        case _:
            return PostgresAgentStore(session_factory)
