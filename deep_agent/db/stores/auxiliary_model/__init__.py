from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.auxiliary_model.base import AuxiliaryModelStoreBase
from deep_agent.db.stores.auxiliary_model.mysql import MySQLAuxiliaryModelStore
from deep_agent.db.stores.auxiliary_model.postgres import PostgresAuxiliaryModelStore
from deep_agent.db.stores.auxiliary_model.sqlite import SqliteAuxiliaryModelStore

__all__ = ["AuxiliaryModelStoreBase", "create_auxiliary_model_store"]


def create_auxiliary_model_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> AuxiliaryModelStoreBase:
    match dialect:
        case DbDialect.MYSQL:
            return MySQLAuxiliaryModelStore(session_factory)
        case DbDialect.SQLITE:
            return SqliteAuxiliaryModelStore(session_factory)
        case _:
            return PostgresAuxiliaryModelStore(session_factory)
