from __future__ import annotations

import asyncio

from alembic import command
from alembic.config import Config

from deep_agent.core.config import get_database_url, get_settings
from deep_agent.core.logging import get_logger
from deep_agent.core.paths import REPO_ROOT

logger = get_logger(__name__)

_ALEMBIC_INI = REPO_ROOT / "alembic.ini"


def _build_alembic_config() -> Config:
    cfg = Config(str(_ALEMBIC_INI))
    database_url = get_database_url()
    if database_url:
        cfg.set_main_option("sqlalchemy.url", database_url)
    return cfg


def upgrade_database_sync(revision: str = "head") -> None:
    """Run Alembic upgrade in the current thread."""
    get_settings()
    if not get_database_url():
        return
    logger.info("Running database migrations (upgrade %s)", revision)
    command.upgrade(_build_alembic_config(), revision)
    logger.info("Database migrations complete")


async def upgrade_database(revision: str = "head") -> None:
    """Run Alembic upgrade without blocking the event loop."""
    get_settings()
    if not get_database_url():
        return
    await asyncio.to_thread(upgrade_database_sync, revision)
