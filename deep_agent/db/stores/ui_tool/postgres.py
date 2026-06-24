from __future__ import annotations

from sqlalchemy import cast, or_
from sqlalchemy.dialects.postgresql import JSONB

from deep_agent.db.models import UiToolDefinition
from deep_agent.db.stores.ui_tool.base import UiToolStoreBase


class PostgresUiToolStore(UiToolStoreBase):
    _sql_agent_id_filter = True

    def _apply_agent_id_filter(self, stmt, agent_id: str):
        return stmt.where(
            or_(
                self._agent_id_unrestricted(),
                cast(UiToolDefinition.agent_ids, JSONB).contains([agent_id]),
            )
        )
