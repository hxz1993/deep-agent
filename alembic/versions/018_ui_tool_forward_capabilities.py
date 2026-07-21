"""move UI tool assignment to agent capabilities

Revision ID: 018
Revises: 017
Create Date: 2026-07-21

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import has_column, is_sqlite, text_json_type

revision: str = "018"
down_revision: Union[str, Sequence[str], None] = "017"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "ui_tool_definitions", "agent_ids"):
        return
    if is_sqlite(bind):
        with op.batch_alter_table("ui_tool_definitions") as batch_op:
            batch_op.drop_column("agent_ids")
        return
    op.drop_column("ui_tool_definitions", "agent_ids")


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "ui_tool_definitions", "agent_ids"):
        return
    column = sa.Column("agent_ids", text_json_type(bind), nullable=True)
    if is_sqlite(bind):
        with op.batch_alter_table("ui_tool_definitions") as batch_op:
            batch_op.add_column(column)
        return
    op.add_column("ui_tool_definitions", column)
