from __future__ import annotations

from deep_agent.db.stores.agent.base import AgentStoreBase


class PostgresAgentStore(AgentStoreBase):
    """ponytail: identical to base until Postgres-specific SQL is needed."""
