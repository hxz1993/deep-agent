from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect, resolve_dialect
from deep_agent.db.engine import create_engine
from deep_agent.db.migrate import upgrade_database
from deep_agent.db.stores.bundle import DataStores, create_data_stores
from deep_agent.db.url import get_database_url


@dataclass(frozen=True)
class DatabaseBundle:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    stores: DataStores
    dialect: DbDialect


async def bootstrap_database() -> DatabaseBundle | None:
    """Run migrations, create engine, and select store backends."""
    database_url = get_database_url()
    if database_url is None:
        return None

    await upgrade_database()
    engine, session_factory = create_engine(database_url)
    dialect = resolve_dialect(database_url)
    stores = create_data_stores(dialect, session_factory)
    return DatabaseBundle(
        engine=engine,
        session_factory=session_factory,
        stores=stores,
        dialect=dialect,
    )
