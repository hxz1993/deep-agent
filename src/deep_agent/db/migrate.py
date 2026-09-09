from __future__ import annotations

import asyncio

from alembic import command
from alembic.config import Config

from loguru import logger

from deep_agent.core.logging import configure_logging
from deep_agent.core.paths import REPO_ROOT
from deep_agent.db.url import escape_for_ini, get_database_url

_ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _build_alembic_config() -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", escape_for_ini(get_database_url()))
    return cfg


def upgrade_database_sync(revision: str = "head") -> None:
    """Run Alembic upgrade in the current thread."""
    configure_logging()
    logger.info("Running database migrations (upgrade {})", revision)
    try:
        command.upgrade(_build_alembic_config(), revision)
    except Exception as exc:
        logger.exception("Database migration failed (upgrade {}): {}", revision, exc)
        raise
    logger.info("Database migrations complete")


async def upgrade_database(revision: str = "head") -> None:
    """Run Alembic upgrade without blocking the event loop."""
    await asyncio.to_thread(upgrade_database_sync, revision)
