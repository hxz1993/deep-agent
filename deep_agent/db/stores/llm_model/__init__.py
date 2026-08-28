from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.llm_model.base import (
    LlmModelStoreBase,
    LlmModelValidationError,
)
from deep_agent.db.stores.llm_model.mysql import MySQLLlmModelStore
from deep_agent.db.stores.llm_model.postgres import PostgresLlmModelStore
from deep_agent.db.stores.llm_model.sqlite import SqliteLlmModelStore

__all__ = [
    "LlmModelStoreBase",
    "LlmModelValidationError",
    "create_llm_model_store",
]


def create_llm_model_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> LlmModelStoreBase:
    match dialect:
        case DbDialect.MYSQL:
            return MySQLLlmModelStore(session_factory)
        case DbDialect.SQLITE:
            return SqliteLlmModelStore(session_factory)
        case _:
            return PostgresLlmModelStore(session_factory)
