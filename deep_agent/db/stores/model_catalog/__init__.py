from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.model_catalog.base import ModelCatalogStoreBase
from deep_agent.db.stores.model_catalog.mysql import MySQLModelCatalogStore
from deep_agent.db.stores.model_catalog.postgres import PostgresModelCatalogStore
from deep_agent.db.stores.model_catalog.sqlite import SqliteModelCatalogStore

__all__ = ["ModelCatalogStoreBase", "create_model_catalog_store"]


def create_model_catalog_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> ModelCatalogStoreBase:
    match dialect:
        case DbDialect.MYSQL:
            return MySQLModelCatalogStore(session_factory)
        case DbDialect.SQLITE:
            return SqliteModelCatalogStore(session_factory)
        case _:
            return PostgresModelCatalogStore(session_factory)
