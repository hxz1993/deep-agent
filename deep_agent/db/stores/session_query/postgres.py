from __future__ import annotations

from deep_agent.db.stores.session_query.base import SessionQueryStoreBase


class PostgresSessionQueryStore(SessionQueryStoreBase):
    """ponytail: identical to base until Postgres-specific SQL is needed."""
