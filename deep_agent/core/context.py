from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deep_agent.capabilities import build_registry, load_and_register_mcp
from deep_agent.capabilities.mcp.session_pool import McpSessionPool
from deep_agent.capabilities.registry import CapabilityRegistry
from deep_agent.core.config import Settings, get_database_url, get_settings
from deep_agent.core.logging import get_logger
from deep_agent.db.engine import close_db, create_engine
from deep_agent.db.migrate import upgrade_database
from deep_agent.memory import InMemorySessionStore, MySQLSessionStore, PostgresSessionStore
from deep_agent.memory.base import SessionStore

logger = get_logger(__name__)

_context: AppContext | None = None


@dataclass
class AppContext:
    settings: Settings
    registry: CapabilityRegistry
    store: SessionStore
    mcp_session_pool: McpSessionPool
    db_engine: AsyncEngine | None = None
    db_session_factory: async_sessionmaker[AsyncSession] | None = None

    @classmethod
    async def bootstrap(cls) -> AppContext:
        settings = get_settings()
        database_url = get_database_url()
        registry = build_registry(settings)
        mcp_session_pool = McpSessionPool()

        ctx = cls(
            settings=settings,
            registry=registry,
            store=InMemorySessionStore(),
            mcp_session_pool=mcp_session_pool,
        )
        init_context(ctx)

        if database_url:
            try:
                await upgrade_database()
            except Exception:
                logger.exception("Startup aborted: database migration failed")
                raise
            db_engine, db_session_factory = create_engine(database_url)
            if database_url.startswith("mysql+"):
                ctx.store = MySQLSessionStore(db_session_factory)
                db_kind = "mysql"
            else:
                ctx.store = PostgresSessionStore(db_session_factory)
                db_kind = "postgres"
            ctx.db_engine = db_engine
            ctx.db_session_factory = db_session_factory
            try:
                async with db_session_factory() as session:
                    await load_and_register_mcp(registry, session)
            except Exception:
                logger.exception("Failed to load MCP servers from database.")
            logger.info("%s started (model=%s, database=%s)", settings.app_name, settings.model, db_kind)
        else:
            logger.warning("Database not configured; MCP servers will not be loaded.")
            logger.info("%s started (model=%s, database=in-memory)", settings.app_name, settings.model)

        return ctx

    @classmethod
    def for_testing(
        cls,
        *,
        settings: Settings | None = None,
        registry: CapabilityRegistry | None = None,
        store: SessionStore | None = None,
        mcp_session_pool: McpSessionPool | None = None,
        db_engine: AsyncEngine | None = None,
        db_session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> AppContext:
        return cls(
            settings=settings or get_settings(),
            registry=registry or CapabilityRegistry(),
            store=store or InMemorySessionStore(),
            mcp_session_pool=mcp_session_pool or McpSessionPool(),
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
