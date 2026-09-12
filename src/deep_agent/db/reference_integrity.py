"""Transactional reference guards for the foreign-key-free schema.

Lock the referenced parent before checking/writing its children. Configuration
updates lock old and new parents in sorted key order and re-read the child after
locking; a changed parent is a retryable conflict, never an orphan write.
SQLite callers use BEGIN IMMEDIATE in SessionBoundMixin/the coordinator.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import lazyload


class ReferenceConflict(ValueError):
    """A referenced record disappeared, changed, or is still in use."""


async def lock_reference(db, model, key, value, label: str):
    row = (await db.execute(
        select(model).options(lazyload('*')).where(key == value)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()
    if row is None:
        raise ReferenceConflict(f"{label} not found: {value}")
    return row


async def lock_optional(db, model, key, value):
    return (await db.execute(
        select(model).options(lazyload('*')).where(key == value)
        .with_for_update().execution_options(populate_existing=True)
    )).scalar_one_or_none()


def is_unique_conflict(exc: IntegrityError) -> bool:
    orig = exc.orig
    code = getattr(orig, 'sqlstate', None) or getattr(orig, 'pgcode', None)
    if code is not None:
        return code == '23505'
    args = getattr(orig, 'args', ())
    if args and isinstance(args[0], int):
        return args[0] in (1062, 1555, 2067)
    message = str(orig).lower()
    return 'unique constraint failed' in message or 'duplicate key value violates unique constraint' in message


async def acquire_write_barrier(db, *, exclusive: bool = False) -> None:
    """Configuration replacement excludes ordinary writes across API workers.

    The migration revision is a stable row outside the replacement scope. Normal
    transactions take a shared lock (so they remain concurrent); import takes an
    exclusive lock before touching parents. SQLite already uses BEGIN IMMEDIATE.
    Bare metadata-only test databases have no revision row and need no importer.
    """
    from sqlalchemy import Column, MetaData, String, Table, inspect
    connection = await db.connection() if hasattr(db, 'get_bind') else db
    if connection.dialect.name == 'sqlite':
        return
    cache_key = 'deepagent_has_revision_table'
    if cache_key not in connection.info:
        connection.info[cache_key] = await connection.run_sync(
            lambda conn: inspect(conn).has_table('alembic_version'))
    if not connection.info[cache_key]:
        return
    revision = Table('alembic_version', MetaData(), Column('version_num', String(32)))
    await connection.execute(select(revision.c.version_num).with_for_update(read=not exclusive))
