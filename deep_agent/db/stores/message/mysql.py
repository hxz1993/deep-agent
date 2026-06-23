from __future__ import annotations

from deep_agent.db.stores.message.base import MessageStoreBase


class MySQLMessageStore(MessageStoreBase):
    """ponytail: identical to base until MySQL-specific SQL is needed."""
