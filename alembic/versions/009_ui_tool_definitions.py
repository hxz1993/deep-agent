"""ui_tool_definitions table

Revision ID: 009
Revises: 008
Create Date: 2026-06-23

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql

revision: str = "009"
down_revision: Union[str, Sequence[str], None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _text_json_type():
    """Match deep_agent.db.types.JSONType (TextJSON): TEXT / LONGTEXT, not JSONB."""
    if op.get_bind().dialect.name == "mysql":
        return mysql.LONGTEXT()
    return sa.Text()


def _now_default():
    if _is_postgresql():
        return sa.text("now()")
    return sa.text("CURRENT_TIMESTAMP")


def _boolean_true_default():
    if _is_postgresql():
        return sa.text("true")
    return sa.text("1")


def _boolean_false_default():
    if _is_postgresql():
        return sa.text("false")
    return sa.text("0")


def _key_type():
    if _is_postgresql():
        return sa.Text()
    return sa.String(191)


def upgrade() -> None:
    op.create_table(
        "ui_tool_definitions",
        sa.Column("name", _key_type(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("parameters_json_schema", _text_json_type(), nullable=False),
        sa.Column("widget", sa.String(64), nullable=True),
        sa.Column("result_schema", _text_json_type(), nullable=True),
        sa.Column("metadata", _text_json_type(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column(
            "mcp_server_name",
            _key_type(),
            sa.ForeignKey("mcp_servers.name", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("enabled", sa.Boolean(), server_default=_boolean_true_default(), nullable=False),
        sa.Column("override", sa.Boolean(), server_default=_boolean_false_default(), nullable=False),
        sa.Column("agent_ids", _text_json_type(), nullable=True),
        sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_index("ix_ui_tool_definitions_mcp_server", "ui_tool_definitions", ["mcp_server_name"])
    op.create_index("ix_ui_tool_definitions_source_enabled", "ui_tool_definitions", ["source", "enabled"])


def downgrade() -> None:
    op.drop_index("ix_ui_tool_definitions_source_enabled", table_name="ui_tool_definitions")
    op.drop_index("ix_ui_tool_definitions_mcp_server", table_name="ui_tool_definitions")
    op.drop_table("ui_tool_definitions")
