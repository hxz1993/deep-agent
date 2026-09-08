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


MYSQL_POOL_RECYCLE_SECONDS = 90
_CLOSED_TCP_TRANSPORT_MARKERS = (
    "TCPTransport closed=True",
    "handler is closed",
)


@event.listens_for(Engine, "connect")
def _sqlite_pragma_foreign_keys(dbapi_connection, _connection_record) -> None:
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


def _is_asyncmy_closed_transport_error(exc: BaseException) -> bool:
    """Recognize asyncmy's uvloop error for a server-closed idle connection."""
    if not isinstance(exc, RuntimeError):
        return False
    message = str(exc)
    return all(marker in message for marker in _CLOSED_TCP_TRANSPORT_MARKERS)


def _mark_asyncmy_pre_ping_disconnect(exception_context) -> None:
    """Let SQLAlchemy replace the one stale connection during pool pre-ping."""
    if not getattr(exception_context, "is_pre_ping", False):
        return
    if _is_asyncmy_closed_transport_error(exception_context.original_exception):
        exception_context.is_disconnect = True


def _set_mysql_session_utc(dbapi_connection, _connection_record) -> None:
    """Make server defaults and naive MySQL DATETIME values unambiguously UTC."""
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("SET time_zone = '+00:00'")
    finally:
        cursor.close()


def create_engine(database_url: str) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    dialect = resolve_dialect(database_url)
    engine_options: dict[str, object] = {"pool_pre_ping": True}
    if dialect is DbDialect.MYSQL:
        engine_options["pool_recycle"] = MYSQL_POOL_RECYCLE_SECONDS

    engine = create_async_engine(database_url, **engine_options)
    if dialect is DbDialect.MYSQL:
        event.listen(
            engine.sync_engine,
            "connect",
            _set_mysql_session_utc,
            insert=True,
        )
        event.listen(engine.sync_engine, "handle_error", _mark_asyncmy_pre_ping_disconnect)
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
