from __future__ import annotations

from deep_agent.db.stores.message.base import MessageStoreBase


class PostgresMessageStore(MessageStoreBase):
    """ponytail: identical to base until Postgres-specific SQL is needed."""
