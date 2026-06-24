from __future__ import annotations

from deep_agent.db.stores.ui_tool.base import UiToolStoreBase


class MySQLUiToolStore(UiToolStoreBase):
    """agent_ids 存 LONGTEXT（TextJSON）；5.6 / 早期 5.7 无 JSON 函数，沿用 base Python post-filter。"""
