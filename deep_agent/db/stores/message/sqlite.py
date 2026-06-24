from __future__ import annotations

from deep_agent.db.stores.message.base import MessageStoreBase


class SqliteMessageStore(MessageStoreBase):
    """ponytail: identical to base until SQLite-specific SQL is needed."""
