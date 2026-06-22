from __future__ import annotations

from sqlalchemy.engine import URL

from deep_agent.options import OPTION
from deepfos_platform.conf.option import OptionNotSetError


def escape_for_ini(value: str) -> str:
    """Escape ``%`` for ConfigParser values (e.g. Alembic ``alembic.ini``)."""
    return value.replace("%", "%%")


def get_database_url() -> str | None:
    """Build async SQLAlchemy DSN from ``OPTION.dbms``, or return None if unset."""
    try:
        dialect = OPTION.dbms.name
        username = OPTION.dbms.username
        password = OPTION.dbms.password
        host = OPTION.dbms.hostname
        port = OPTION.dbms.port
        database = OPTION.dbms.db_name
    except (OptionNotSetError, KeyError):
        return None

    driver_map = {
        "postgres": "postgresql+asyncpg",
        "mysql": "mysql+asyncmy",
    }
    drivername = driver_map.get(dialect)
    if not drivername:
        return None
    return URL.create(
        drivername=drivername,
        username=username,
        password=password,
        host=host,
        port=port,
        database=database,
    ).render_as_string(hide_password=False)
