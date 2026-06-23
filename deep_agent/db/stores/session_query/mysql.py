from __future__ import annotations

from deep_agent.db.stores.session_query.base import SessionQueryStoreBase


class MySQLSessionQueryStore(SessionQueryStoreBase):
    """ponytail: identical to base until MySQL-specific SQL is needed."""
