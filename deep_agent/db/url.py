from __future__ import annotations

from pathlib import Path

from sqlalchemy.engine import URL

from deep_agent.options import OPTION
from deepfos_platform.conf.option import OptionNotSetError


def escape_for_ini(value: str) -> str:
    """Escape ``%`` for ConfigParser values (e.g. Alembic ``alembic.ini``)."""
    return value.replace("%", "%%")


def sqlite_database_url(path: str | Path | None = None) -> str:
    """Build async SQLAlchemy DSN for the default on-disk SQLite database."""
    db_path = Path(path or OPTION.agent.sqlite_path).resolve()
    return f"sqlite+aiosqlite:///{db_path.as_posix()}"


def ensure_sqlite_parent_dir(database_url: str) -> None:
    """Create parent directory for file-based SQLite URLs."""
    if not database_url.startswith("sqlite+"):
        return
    raw = database_url.split("///", 1)[-1]
    if raw == ":memory:" or raw.startswith(":memory:"):
        return
    Path(raw).parent.mkdir(parents=True, exist_ok=True)


def get_database_url() -> str:
    """Build async SQLAlchemy DSN from ``OPTION.dbms``, or default SQLite."""
    try:
        dialect = OPTION.dbms.name
        username = OPTION.dbms.username
        password = OPTION.dbms.password
        host = OPTION.dbms.hostname
        port = OPTION.dbms.port
        database = OPTION.dbms.db_name
    except (OptionNotSetError, KeyError):
        return sqlite_database_url()

    driver_map = {
        "postgres": "postgresql+asyncpg",
        "mysql": "mysql+asyncmy",
    }
    drivername = driver_map.get(dialect)
    if not drivername:
        return sqlite_database_url()
    return URL.create(
        drivername=drivername,
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    ).render_as_string(hide_password=False)
