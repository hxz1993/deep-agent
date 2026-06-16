from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession


@asynccontextmanager
async def db_session(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if session_factory is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured. Set DEEP_AGENT_DATABASE_URL.",
        )
    async with session_factory() as session:
        yield session


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    async with db_session(request) as session:
        yield session
