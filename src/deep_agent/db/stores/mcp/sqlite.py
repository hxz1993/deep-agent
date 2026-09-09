from __future__ import annotations

from deep_agent.db.stores.mcp.base import McpServerStoreBase


class SqliteMcpServerStore(McpServerStoreBase):
    """ponytail: identical to base until SQLite-specific SQL is needed."""
