from __future__ import annotations

import asyncio
import contextlib
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


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
        db = self._require_factory()()
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
        db = self._require_factory()()
        await db.__aenter__()
        exc_info: tuple[type[BaseException] | None, BaseException | None, Any] = (
            None,
            None,
            None,
        )
        try:
            try:
                yield db
                await asyncio.shield(db.commit())
            except BaseException:
                exc_info = sys.exc_info()
                await _shield_rollback(db)
                raise
        finally:
            await _shield_exit(db, *exc_info)
