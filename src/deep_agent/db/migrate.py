from __future__ import annotations

import asyncio

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from deep_agent.db.codex_schema import ensure_codex_columns
from deep_agent.db.session_source_schema import ensure_session_source_columns

from loguru import logger

from deep_agent.core.logging import configure_logging
from deep_agent.core.paths import REPO_ROOT
from deep_agent.db.url import escape_for_ini, get_database_url

_ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _build_alembic_config() -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    database_url = get_database_url()
    cfg.attributes["database_url"] = database_url
    cfg.set_main_option("sqlalchemy.url", escape_for_ini(database_url))
    return cfg


async def _repair_unpublished_schema(database_url: str) -> None:
    engine = create_async_engine(database_url)
    try:
        async with engine.begin() as connection:
            def repair(sync_connection):
                version = sync_connection.scalar(text("SELECT version_num FROM alembic_version"))
                if version == "007":
                    operations = Operations(MigrationContext.configure(sync_connection))
                    ensure_codex_columns(operations)
                elif version == "010":
                    ensure_session_source_columns(Operations(MigrationContext.configure(sync_connection)))
            await connection.run_sync(repair)
    finally:
        await engine.dispose()


def upgrade_database_sync(revision: str = "head") -> None:
    """Run Alembic upgrade in the current thread."""
    configure_logging()
    logger.info("Running database migrations (upgrade {})", revision)
    try:
        config = _build_alembic_config()
        command.upgrade(config, revision)
        if revision in {"head", "010"}:
            # Unpublished same-revision schemas must use exactly the migration target.
            asyncio.run(_repair_unpublished_schema(config.attributes["database_url"]))
    except Exception as exc:
        logger.exception("Database migration failed (upgrade {}): {}", revision, exc)
        raise
    logger.info("Database migrations complete")


async def upgrade_database(revision: str = "head") -> None:
    """Run Alembic upgrade without blocking the event loop."""
    await asyncio.to_thread(upgrade_database_sync, revision)
