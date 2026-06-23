from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


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
        async with self._require_factory()() as db:
            yield db

    @asynccontextmanager
    async def _write(self) -> AsyncIterator[AsyncSession]:
        async with self._require_factory()() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise
