from __future__ import annotations

from enum import StrEnum


class DbDialect(StrEnum):
    POSTGRES = "postgres"
    MYSQL = "mysql"
    SQLITE = "sqlite"


def resolve_dialect(database_url: str) -> DbDialect:
    """Map an async SQLAlchemy URL to a logical dialect."""
    if database_url.startswith("sqlite+"):
        return DbDialect.SQLITE
    if database_url.startswith("mysql+"):
        return DbDialect.MYSQL
    return DbDialect.POSTGRES
