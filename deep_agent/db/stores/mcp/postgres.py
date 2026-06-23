from __future__ import annotations

from deep_agent.db.stores.mcp.base import McpServerStoreBase


class PostgresMcpServerStore(McpServerStoreBase):
    """ponytail: identical to base until Postgres-specific SQL is needed."""
