from __future__ import annotations

from collections.abc import AsyncIterator

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession


async def get_db(request: Request) -> AsyncIterator[AsyncSession]:
    session_factory = getattr(request.app.state, "db_session_factory", None)
    if session_factory is None:
        from fastapi import HTTPException, status

        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is not configured. Set DEEP_AGENT_DATABASE_URL.",
        )
    async with session_factory() as session:
        yield session
