from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect, resolve_dialect
from deep_agent.db.engine import create_engine
from deep_agent.db.migrate import upgrade_database
from deep_agent.db.stores.base import SessionStore
from deep_agent.db.stores.in_memory import InMemorySessionStore
from deep_agent.db.stores.mysql import MySQLSessionStore
from deep_agent.db.stores.postgres import PostgresSessionStore
from deep_agent.db.url import get_database_url


@dataclass(frozen=True)
class DatabaseBundle:
    engine: AsyncEngine
    session_factory: async_sessionmaker[AsyncSession]
    store: SessionStore
    dialect: DbDialect


def create_session_store(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> SessionStore:
    match dialect:
        case DbDialect.MYSQL:
            return MySQLSessionStore(session_factory)
        case DbDialect.POSTGRES:
            return PostgresSessionStore(session_factory)
        case _:
            return InMemorySessionStore()


async def bootstrap_database() -> DatabaseBundle | None:
    """Run migrations, create engine, and select a session store backend."""
    database_url = get_database_url()
    if database_url is None:
        return None

    await upgrade_database()
    engine, session_factory = create_engine(database_url)
    dialect = resolve_dialect(database_url)
    store = create_session_store(dialect, session_factory)
    return DatabaseBundle(
        engine=engine,
        session_factory=session_factory,
        store=store,
        dialect=dialect,
    )
