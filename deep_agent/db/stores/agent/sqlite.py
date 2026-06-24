from __future__ import annotations

from deep_agent.db.stores.agent.base import AgentStoreBase


class SqliteAgentStore(AgentStoreBase):
    """ponytail: identical to base until SQLite-specific SQL is needed."""
