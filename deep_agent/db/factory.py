from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from deep_agent.db.dialect import DbDialect, resolve_dialect
from deep_agent.db.migrate import upgrade_database
from deep_agent.db.stores.bundle import DataStores, create_data_stores
from deep_agent.db.url import ensure_sqlite_parent_dir, get_database_url


@event.listens_for(Engine, "connect")
def _sqlite_pragma_foreign_keys(dbapi_connection, _connection_record) -> None:
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def create_engine(database_url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, session_factory


async def close_db(engine: AsyncEngine) -> None:
    await engine.dispose()


@dataclass(frozen=True)
class DatabaseBundle:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    stores: DataStores
    dialect: DbDialect


async def bootstrap_database() -> DatabaseBundle:
    """Run migrations, create engine, and select store backends."""
    database_url = get_database_url()
    ensure_sqlite_parent_dir(database_url)

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
