from __future__ import annotations

from enum import StrEnum


class DbDialect(StrEnum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    MEMORY = "memory"


def resolve_dialect(database_url: str | None) -> DbDialect:
    """Map an async SQLAlchemy URL to a logical dialect, or MEMORY when unset."""
    if not database_url:
        return DbDialect.MEMORY
    if database_url.startswith("mysql+"):
        return DbDialect.MYSQL
    return DbDialect.POSTGRES
