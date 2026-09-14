"""Explicit loopback-only preview without deployment startup side effects."""
from __future__ import annotations

import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from alembic.script import ScriptDirectory
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from deep_agent.db.dialect import resolve_dialect
from deep_agent.db.factory import create_engine
from deep_agent.db.migrate import _build_alembic_config
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.db.url import get_database_url
from deep_agent.local_security import local_runtime_enabled


def enforce_read_only(engine: AsyncEngine) -> None:
    """Apply a database-side guard to every new pooled connection."""
    if engine.dialect.name == "postgresql":
        # Transaction-level protection also survives pooled rollbacks.
        @event.listens_for(engine.sync_engine, "begin")
        def configure_transaction(connection):
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
        return

    commands = {
        "mysql": "SET SESSION TRANSACTION READ ONLY",
        "sqlite": "PRAGMA query_only = ON",
    }
    command = commands[engine.dialect.name]

    @event.listens_for(engine.sync_engine, "connect")
    def configure_connection(connection, _record):
        cursor = connection.cursor()
        try:
            cursor.execute(command)
        finally:
            cursor.close()


@asynccontextmanager
async def preview_lifespan(_app):
    if not local_runtime_enabled():
        raise RuntimeError("Read-only preview requires the local_dev entry point")

    from deep_agent.agent.active_runs import ActiveRunRegistry
    from deep_agent.agent.event_persister import EventPersister
    from deep_agent.capabilities import build_registry
    from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
    from deep_agent.core.context import AppContext, init_context, reset_context
    from deep_agent.files.storage import PlatformFileStore
    from deep_agent.security import ConsoleAdminAuthorizer
    from deep_agent.skills.runtime import SkillRuntimeLibrary

    url = get_database_url()
    engine, session_factory = create_engine(url)
    enforce_read_only(engine)
    ctx = None
    # Never share or reconcile the ordinary runtime Skill directory.
    with tempfile.TemporaryDirectory(prefix="deep-agent-preview-TMP_to_delete-") as runtime_dir:
        try:
            async with engine.connect() as connection:
                current = set((await connection.execute(text(
                    "SELECT version_num FROM alembic_version"
                ))).scalars())
            expected = set(ScriptDirectory.from_config(_build_alembic_config()).get_heads())
            if current != expected:
                raise RuntimeError(
                    "Read-only preview requires an existing database at the code migration head"
                )
            stores = create_data_stores(resolve_dialect(url), session_factory)
            file_store = PlatformFileStore()
            ctx = AppContext(
                registry=build_registry(stores=stores), stores=stores,
                mcp_lease_manager=McpLeaseManager(),
                event_persister=EventPersister(stores.coordinator),
                db_engine=engine, db_session_factory=session_factory,
                active_runs=ActiveRunRegistry(),
                skill_runtime=SkillRuntimeLibrary(Path(runtime_dir), file_store=file_store),
                console_admin_authorizer=ConsoleAdminAuthorizer(), file_store=file_store,
            )
            # Do not migrate, repair compactions, reconcile Skills, start the
            # persister, or initialize a sandbox/gateway in this entry point.
            init_context(ctx)
            yield
        finally:
            if ctx is not None:
                await ctx.aclose()
            else:
                await engine.dispose()
            reset_context()
