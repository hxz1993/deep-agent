from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.capabilities.registry import CapabilityRegistry
from loguru import logger

from deep_agent.db.factory import bootstrap_database, close_db
from deep_agent.db.stores.bundle import DataStores
from deep_agent.db.conversation import SessionStore
from deep_agent.options import OPTION

_context: AppContext | None = None


@dataclass
class AppContext:
    registry: CapabilityRegistry
    stores: DataStores
    mcp_lease_manager: McpLeaseManager
    db_engine: AsyncEngine
    db_session_factory: async_sessionmaker[AsyncSession]

    @property
    def store(self) -> SessionStore:
        return self.stores.coordinator

    @classmethod
    async def bootstrap(cls) -> AppContext:
        mcp_lease_manager = McpLeaseManager(mcp_acquire_retries=OPTION.agent.mcp_acquire_retries)

        try:
            bundle = await bootstrap_database()
        except Exception:
            logger.exception("Startup aborted: database migration failed")
            raise

        logger.info(
            "{} started (database={})",
            OPTION.agent.app_name,
            bundle.dialect.value,
        )

        ctx = cls(
            registry=build_registry(stores=bundle.stores),
            stores=bundle.stores,
            mcp_lease_manager=mcp_lease_manager,
            db_engine=bundle.engine,
            db_session_factory=bundle.session_factory,
        )
        init_context(ctx)
        return ctx

    @classmethod
    def for_testing(
        cls,
        *,
        registry: CapabilityRegistry | None = None,
        stores: DataStores | None = None,
        mcp_lease_manager: McpLeaseManager | None = None,
        mcp_acquire_retries: int | None = None,
        db_engine: AsyncEngine | None = None,
        db_session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> AppContext:
        if mcp_lease_manager is None:
            retries = (
                OPTION.agent.mcp_acquire_retries
                if mcp_acquire_retries is None
                else mcp_acquire_retries
            )
            mcp_lease_manager = McpLeaseManager(mcp_acquire_retries=retries)
        if stores is None:
            raise ValueError("for_testing requires explicit stores (use tests.helpers.sqlite_stores)")
        if db_engine is None or db_session_factory is None:
            raise ValueError("for_testing requires db_engine and db_session_factory when using SQL stores")
        return cls(
            registry=registry or CapabilityRegistry(),
            stores=stores,
            mcp_lease_manager=mcp_lease_manager,
            db_engine=db_engine,
            db_session_factory=db_session_factory,
        )

    async def aclose(self) -> None:
        await self.mcp_lease_manager.close_all()
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
