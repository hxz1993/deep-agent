from __future__ import annotations

from deep_agent.db.reference_integrity import acquire_write_barrier

import asyncio
import contextlib
import sys
import weakref
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


_SQLITE_SESSION_LOCKS: weakref.WeakKeyDictionary[
    async_sessionmaker[AsyncSession], asyncio.Lock
] = weakref.WeakKeyDictionary()


def sqlite_session_lock(
    session_factory: async_sessionmaker[AsyncSession],
) -> asyncio.Lock | None:
    """Serialize sessions that share SQLite's single in-memory connection.

    Production MySQL/PostgreSQL databases retain their normal concurrent session
    behavior. SQLite, including the StaticPool used by local development and unit
    tests, cannot safely let separate AsyncSession objects overlap on one
    connection: closing a reader can otherwise roll back another writer.
    """

    bind = session_factory.kw.get("bind")
    if bind is None or bind.dialect.name != "sqlite":
        return None
    lock = _SQLITE_SESSION_LOCKS.get(session_factory)
    if lock is None:
        lock = asyncio.Lock()
        _SQLITE_SESSION_LOCKS[session_factory] = lock
    return lock


@asynccontextmanager
async def serialized_sqlite_session(
    session_factory: async_sessionmaker[AsyncSession],
) -> AsyncIterator[None]:
    lock = sqlite_session_lock(session_factory)
    if lock is None:
        yield
        return
    async with lock:
        yield


async def _shield_rollback(db: AsyncSession) -> None:
    with contextlib.suppress(Exception):
        await asyncio.shield(db.rollback())


async def _shield_exit(
    db: AsyncSession,
    exc_type: type[BaseException] | None,
    exc: BaseException | None,
    tb: Any,
) -> None:
    with contextlib.suppress(Exception):
        await asyncio.shield(db.__aexit__(exc_type, exc, tb))


class SessionBoundMixin:
    """Bind ``session_factory`` and open read/write DB sessions."""

    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None) -> None:
        self._session_factory = session_factory

    def _require_factory(self) -> async_sessionmaker[AsyncSession]:
        if self._session_factory is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=(
                    "Database is not configured. "
                    "Set DEEP_AGENT_DBMS_NAME/USER/PASSWORD/HOSTNAME/PORT/DATABASE."
                ),
            )
        return self._session_factory

    @asynccontextmanager
    async def _read(self) -> AsyncIterator[AsyncSession]:
        # ponytail: shield __aexit__ so SSE cancel does not leak pool connections
        factory = self._require_factory()
        async with serialized_sqlite_session(factory):
            db = factory()
            await db.__aenter__()
            exc_info: tuple[type[BaseException] | None, BaseException | None, Any] = (
                None,
                None,
                None,
            )
            try:
                try:
                    yield db
                except BaseException:
                    exc_info = sys.exc_info()
                    await _shield_rollback(db)
                    raise
            finally:
                await _shield_exit(db, *exc_info)

    @asynccontextmanager
    async def _write(self) -> AsyncIterator[AsyncSession]:
        factory = self._require_factory()
        async with serialized_sqlite_session(factory):
            db = factory()
            await db.__aenter__()
            exc_info: tuple[type[BaseException] | None, BaseException | None, Any] = (
                None,
                None,
                None,
            )
            try:
                try:
                    if db.get_bind().dialect.name == "sqlite":
                        await db.execute(text("BEGIN IMMEDIATE"))
                    await acquire_write_barrier(db)
                    yield db
                    await asyncio.shield(db.commit())
                except BaseException:
                    exc_info = sys.exc_info()
                    await _shield_rollback(db)
                    raise
            finally:
                await _shield_exit(db, *exc_info)
