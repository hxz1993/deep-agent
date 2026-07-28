from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.context_checkpoint.base import (
    ContextCheckpointSnapshot,
    ContextCheckpointStoreBase,
)
from deep_agent.db.stores.context_checkpoint.mysql import MySQLContextCheckpointStore
from deep_agent.db.stores.context_checkpoint.postgres import PostgresContextCheckpointStore
from deep_agent.db.stores.context_checkpoint.sqlite import SqliteContextCheckpointStore

__all__ = [
    "ContextCheckpointSnapshot",
    "ContextCheckpointStoreBase",
    "create_context_checkpoint_store",
]


def create_context_checkpoint_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> ContextCheckpointStoreBase:
    match dialect:
        case DbDialect.MYSQL:
            return MySQLContextCheckpointStore(session_factory)
        case DbDialect.SQLITE:
            return SqliteContextCheckpointStore(session_factory)
        case _:
            return PostgresContextCheckpointStore(session_factory)
