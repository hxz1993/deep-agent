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
from deep_agent.db.factory import bootstrap_database, close_db
from deep_agent.db.stores.bundle import DataStores, create_data_stores
from deep_agent.db.conversation import InMemoryConversationCoordinator, SessionStore
from deep_agent.options import OPTION

_context: AppContext | None = None


@dataclass
class AppContext:
    registry: CapabilityRegistry
    stores: DataStores
    mcp_session_pool: McpSessionPool
    db_engine: AsyncEngine | None = None
    db_session_factory: async_sessionmaker[AsyncSession] | None = None

    @property
    def store(self) -> SessionStore:
        return self.stores.coordinator

    @classmethod
    async def bootstrap(cls) -> AppContext:
        registry = build_registry()
        mcp_session_pool = McpSessionPool(mcp_acquire_retries=OPTION.agent.mcp_acquire_retries)

        try:
            bundle = await bootstrap_database()
        except Exception:
            logger.exception("Startup aborted: database migration failed")
            raise

        if bundle is not None:
            stores = bundle.stores
            db_engine = bundle.engine
            db_session_factory = bundle.session_factory
        else:
            stores = create_data_stores(DbDialect.MEMORY, None)
            logger.warning("Database not configured; MCP servers will not be loaded.")
            logger.info(
                "{} started (model={}, database=in-memory)",
                OPTION.agent.app_name,
                OPTION.agent.model,
            )
            db_engine = None
            db_session_factory = None

        ctx = cls(
            registry=registry,
            stores=stores,
            mcp_session_pool=mcp_session_pool,
            db_engine=db_engine,
            db_session_factory=db_session_factory,
        )
        init_context(ctx)

        if (
            bundle is not None
            and (mcp_store := bundle.stores.mcp) is not None
        ):
            try:
                await load_and_register_mcp(registry, mcp_store)
                logger.info(
                    "{} started (model={}, database={})",
                    OPTION.agent.app_name,
                    OPTION.agent.model,
                    bundle.dialect.value,
                )
            except Exception:  # noqa
                logger.exception("Failed to load MCP servers from database.")
        return ctx

    @classmethod
    def for_testing(
        cls,
        *,
        registry: CapabilityRegistry | None = None,
        store: SessionStore | None = None,
        stores: DataStores | None = None,
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
        if stores is None:
            coordinator = store or InMemoryConversationCoordinator()
            stores = DataStores(
                chat_session=None,
                message=None,
                coordinator=coordinator,
                agent=None,
                mcp=None,
                ui_tool=None,
            )
        return cls(
            registry=registry or CapabilityRegistry(),
            stores=stores,
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
