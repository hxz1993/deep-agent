from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from deep_agent.db.dialect import DbDialect
from deep_agent.db.models import Base
from deep_agent.db.stores.bundle import DataStores, create_data_stores
from deep_agent.schemas.llm_model import LlmModelCreate

DEFAULT_TEST_LLM_MODEL = "test-default"
DEFAULT_TEST_LLM_MODEL_STRING = "test"


async def seed_default_llm_model(stores: DataStores) -> None:
    """Ensure chat integration tests can resolve the default DB-backed model."""
    if await stores.llm_model.list_all():
        return
    await stores.llm_model.create_unique(
        LlmModelCreate(name=DEFAULT_TEST_LLM_MODEL, model=DEFAULT_TEST_LLM_MODEL_STRING),
    )


async def make_sqlite_stores(
    url: str = "sqlite+aiosqlite:///:memory:",
) -> tuple[DataStores, AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    stores = create_data_stores(DbDialect.SQLITE, session_factory)
    await seed_default_llm_model(stores)
    return stores, engine, session_factory


def make_sqlite_stores_sync(
    url: str = "sqlite+aiosqlite:///:memory:",
) -> tuple[DataStores, AsyncEngine, async_sessionmaker[AsyncSession]]:
    return asyncio.run(make_sqlite_stores(url))
