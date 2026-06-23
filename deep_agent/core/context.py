from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deep_agent.capabilities import build_registry, load_and_register_mcp
from deep_agent.capabilities.mcp.session_pool import McpSessionPool
from deep_agent.capabilities.registry import CapabilityRegistry
from loguru import logger

from deep_agent.db.dialect import DbDialect
from deep_agent.db.engine import close_db
from deep_agent.db.factory import bootstrap_database
from deep_agent.db.stores.base import SessionStore
from deep_agent.db.stores.in_memory import InMemorySessionStore
from deep_agent.options import OPTION

_context: AppContext | None = None


@dataclass
class AppContext:
    registry: CapabilityRegistry
    store: SessionStore
    mcp_session_pool: McpSessionPool
    db_engine: AsyncEngine | None = None
    db_session_factory: async_sessionmaker[AsyncSession] | None = None

    @classmethod
    async def bootstrap(cls) -> AppContext:
        registry = build_registry()
        mcp_session_pool = McpSessionPool(mcp_acquire_retries=OPTION.agent.mcp_acquire_retries)

        ctx = cls(
            registry=registry,
            store=InMemorySessionStore(),
            mcp_session_pool=mcp_session_pool,
        )
        init_context(ctx)

        try:
            bundle = await bootstrap_database()
        except Exception:
            logger.exception("Startup aborted: database migration failed")
            raise

        if bundle is not None:
            ctx.store = bundle.store
            ctx.db_engine = bundle.engine
            ctx.db_session_factory = bundle.session_factory
            db_kind = (
                "mysql"
                if bundle.dialect is DbDialect.MYSQL
                else "postgres"
            )
            try:
                async with bundle.session_factory() as session:
                    await load_and_register_mcp(registry, session)
            except Exception:
                logger.exception("Failed to load MCP servers from database.")
            logger.info(
                "{} started (model={}, database={})",
                OPTION.agent.app_name,
                OPTION.agent.model,
                db_kind,
            )
        else:
            logger.warning("Database not configured; MCP servers will not be loaded.")
            logger.info(
                "{} started (model={}, database=in-memory)",
                OPTION.agent.app_name,
                OPTION.agent.model,
            )

        return ctx

    @classmethod
    def for_testing(
        cls,
        *,
        registry: CapabilityRegistry | None = None,
        store: SessionStore | None = None,
        mcp_session_pool: McpSessionPool | None = None,
        mcp_acquire_retries: int | None = None,
        db_engine: AsyncEngine | None = None,
        db_session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> AppContext:
        if mcp_session_pool is None:
            retries = (
                OPTION.agent.mcp_acquire_retries
                if mcp_acquire_retries is None
                else mcp_acquire_retries
            )
            mcp_session_pool = McpSessionPool(mcp_acquire_retries=retries)
        return cls(
            registry=registry or CapabilityRegistry(),
            store=store or InMemorySessionStore(),
            mcp_session_pool=mcp_session_pool,
            db_engine=db_engine,
            db_session_factory=db_session_factory,
        )

    async def aclose(self) -> None:
        await self.mcp_session_pool.close_all()
        if self.db_engine is not None:
            await close_db(self.db_engine)


def init_context(ctx: AppContext) -> None:
    global _context
    _context = ctx


def reset_context() -> None:
    global _context
    _context = None


def get_context() -> AppContext:
    if _context is None:
        raise RuntimeError("Application context is not initialized")
    return _context


async def get_app_context() -> AppContext:
    return get_context()


AppContextDep = Annotated[AppContext, Depends(get_app_context)]
