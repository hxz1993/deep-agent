from __future__ import annotations

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from deep_agent.core.logging import configure_logging, is_configured
from deep_agent.db.url import escape_for_ini, get_database_url
from deep_agent.db.models import Base
from deep_agent.db.initial_configuration import initialize_current_defaults

config = context.config

if not is_configured():
    configure_logging()

target_metadata = Base.metadata

database_url = config.attributes.get("database_url") or get_database_url()
if database_url:
    config.set_main_option("sqlalchemy.url", escape_for_ini(database_url))


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        run_migrations_with_initial_defaults()


def run_migrations_with_initial_defaults() -> None:
    migration_context = context.get_context()
    # Do not replay defaults on upgrades, restarts, or historical revision targets.
    initialize = (
        not migration_context.get_current_heads()
        and context.get_revision_argument() == context.get_head_revision()
    )
    context.run_migrations()
    if initialize:
        initialize_current_defaults(migration_context.bind)


def do_run_migrations(connection: Connection) -> None:
    # Keep post-migration defaults in the same explicit transaction, including
    # SQLite where Alembic otherwise treats DDL as non-transactional.
    with connection.begin():
        if connection.dialect.name == "sqlite":
            # sqlite3 otherwise defers BEGIN until the first DML, leaving the
            # first batch-rebuild CREATE TABLE outside rollback protection.
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        context.configure(connection=connection, target_metadata=target_metadata)
        run_migrations_with_initial_defaults()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)

    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
