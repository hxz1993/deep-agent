from __future__ import annotations

import asyncio

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from deep_agent.db.dialect import DbDialect
from deep_agent.db.models import Base
from deep_agent.db.stores.bundle import DataStores, create_data_stores


async def make_sqlite_stores(
    url: str = "sqlite+aiosqlite:///:memory:",
) -> tuple[DataStores, AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    stores = create_data_stores(DbDialect.SQLITE, session_factory)
    return stores, engine, session_factory


def make_sqlite_stores_sync(
    url: str = "sqlite+aiosqlite:///:memory:",
) -> tuple[DataStores, AsyncEngine, async_sessionmaker[AsyncSession]]:
    return asyncio.run(make_sqlite_stores(url))
