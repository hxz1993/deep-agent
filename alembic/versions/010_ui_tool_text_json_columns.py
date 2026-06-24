"""ui_tool_definitions: store json columns as text for database portability

Revision ID: 010
Revises: 009
Create Date: 2026-06-23

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op
from deep_agent.db.migration_dialect import existing_json_type, is_postgresql, is_sqlite, text_json_type

revision: str = "010"
down_revision: Union[str, Sequence[str], None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("ui_tool_definitions", "parameters_json_schema", False),
    ("ui_tool_definitions", "result_schema", True),
    ("ui_tool_definitions", "metadata", True),
    ("ui_tool_definitions", "agent_ids", True),
)


def upgrade() -> None:
    bind = op.get_bind()
    target_type = text_json_type(bind)
    existing_type = existing_json_type(bind)

    for table, column, nullable in _JSON_COLUMNS:
        kwargs: dict = {
            "existing_type": existing_type,
            "existing_nullable": nullable,
            "type_": target_type,
        }
        if is_postgresql(bind):
            kwargs["postgresql_using"] = f"{column}::text"
        if is_sqlite(bind):
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column(column, **kwargs)
            continue
        op.alter_column(table, column, **kwargs)


def downgrade() -> None:
    bind = op.get_bind()
    target_type = existing_json_type(bind)
    text_type = text_json_type(bind)

    for table, column, nullable in _JSON_COLUMNS:
        kwargs: dict = {
            "existing_type": text_type,
            "existing_nullable": nullable,
            "type_": target_type,
        }
        if is_postgresql(bind):
            kwargs["postgresql_using"] = f"{column}::jsonb"
        if is_sqlite(bind):
            with op.batch_alter_table(table) as batch_op:
                batch_op.alter_column(column, **kwargs)
            continue
        op.alter_column(table, column, **kwargs)
