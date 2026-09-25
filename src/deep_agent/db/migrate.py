from __future__ import annotations

import asyncio

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from deep_agent.db.codex_schema import ensure_codex_columns

from loguru import logger

from deep_agent.core.logging import configure_logging
from deep_agent.core.paths import REPO_ROOT
from deep_agent.db.url import escape_for_ini, get_database_url

_ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _build_alembic_config() -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", escape_for_ini(get_database_url()))
    return cfg


async def _repair_existing_007() -> None:
    engine = create_async_engine(get_database_url())
    try:
        async with engine.begin() as connection:
            def repair(sync_connection):
                version = sync_connection.scalar(text("SELECT version_num FROM alembic_version"))
                if version == "007":
                    operations = Operations(MigrationContext.configure(sync_connection))
                    ensure_codex_columns(operations)
            await connection.run_sync(repair)
    finally:
        await engine.dispose()


def upgrade_database_sync(revision: str = "head") -> None:
    """Run Alembic upgrade in the current thread."""
    configure_logging()
    logger.info("Running database migrations (upgrade {})", revision)
    try:
        command.upgrade(_build_alembic_config(), revision)
        if revision == "head":
            # Older local databases may already carry the original unpublished
            # 007 marker. Alembic skips its upgrade body for those databases.
            asyncio.run(_repair_existing_007())
    except Exception as exc:
        logger.exception("Database migration failed (upgrade {}): {}", revision, exc)
        raise
    logger.info("Database migrations complete")


async def upgrade_database(revision: str = "head") -> None:
    """Run Alembic upgrade without blocking the event loop."""
    await asyncio.to_thread(upgrade_database_sync, revision)
