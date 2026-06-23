import warnings

warnings.warn(
    "deep_agent.memory is deprecated; use deep_agent.db.stores and deep_agent.db.session_key",
    DeprecationWarning,
    stacklevel=2,
)

from deep_agent.db.session_key import (
    parse_scoped_session_key,
    resolve_session_key,
    scoped_session_key,
)
from deep_agent.db.stores import (
    InMemorySessionStore,
    MySQLSessionStore,
    PostgresSessionStore,
    SessionStore,
    SqlSessionStore,
)

__all__ = [
    "InMemorySessionStore",
    "MySQLSessionStore",
    "PostgresSessionStore",
    "SessionStore",
    "SqlSessionStore",
    "parse_scoped_session_key",
    "resolve_session_key",
    "scoped_session_key",
]
