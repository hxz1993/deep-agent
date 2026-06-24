from __future__ import annotations

from deep_agent.db.stores.ui_tool.base import UiToolStoreBase


class SqliteUiToolStore(UiToolStoreBase):
    """agent_ids 存 TextJSON；沿用 base Python post-filter。"""
