from __future__ import annotations

from deep_agent.db.stores.mcp.base import McpServerStoreBase


class MySQLMcpServerStore(McpServerStoreBase):
    """ponytail: identical to base until MySQL-specific SQL is needed."""
