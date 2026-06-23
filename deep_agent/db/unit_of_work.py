from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.core.context import AppContext
from deep_agent.db.deps import db_session


@asynccontextmanager
async def unit_of_work(ctx: AppContext) -> AsyncIterator[AsyncSession]:
    async with db_session(ctx) as db:
        try:
            yield db
            await db.commit()
        except Exception:
            await db.rollback()
            raise
