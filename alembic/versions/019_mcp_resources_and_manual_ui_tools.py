"""add MCP resource capabilities and remove MCP UI discovery fields

Revision ID: 019
Revises: 018
Create Date: 2026-07-23

"""

from __future__ import annotations

import json
from typing import Any
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import (
    boolean_default,
    has_column,
    is_postgresql,
    is_sqlite,
    key_type,
    text_json_type,
)

revision: str = "019"
down_revision: Union[str, Sequence[str], None] = "018"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _has_index(bind, table_name: str, index_name: str) -> bool:
    return index_name in {item["name"] for item in sa.inspect(bind).get_indexes(table_name)}


def _load_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(value) if isinstance(value, dict) else {}
    return {}


def _remove_legacy_ui_discovery_config(bind) -> None:
    rows = bind.execute(sa.text("SELECT name, config FROM mcp_servers")).mappings()
    for row in rows:
        config = _load_config(row["config"])
        if "ui_tools" not in config:
            continue
        config.pop("ui_tools", None)
        payload = json.dumps(config, ensure_ascii=False)
        if is_postgresql(bind):
            bind.execute(
                sa.text(
                    "UPDATE mcp_servers SET config = CAST(:config AS jsonb), updated_at = now() "
                    "WHERE name = :name"
                ),
                {"name": row["name"], "config": payload},
            )
        else:
            bind.execute(
                sa.text(
                    "UPDATE mcp_servers SET config = :config, updated_at = CURRENT_TIMESTAMP "
                    "WHERE name = :name"
                ),
                {"name": row["name"], "config": payload},
            )


def upgrade() -> None:
    bind = op.get_bind()

    if not has_column(bind, "mcp_servers", "resource_capabilities"):
        op.add_column(
            "mcp_servers",
            sa.Column("resource_capabilities", text_json_type(bind), nullable=True),
        )

    _remove_legacy_ui_discovery_config(bind)

    op.execute(sa.text("DELETE FROM ui_tool_definitions WHERE source = 'mcp'"))

    index_name = "ix_ui_tool_definitions_mcp_server"
    if _has_index(bind, "ui_tool_definitions", index_name):
        op.drop_index(index_name, table_name="ui_tool_definitions")

    obsolete_columns = [
        name
        for name in ("mcp_server_name", "override", "discovered_at")
        if has_column(bind, "ui_tool_definitions", name)
    ]
    if not obsolete_columns:
        return
    if is_sqlite(bind):
        with op.batch_alter_table("ui_tool_definitions") as batch_op:
            for name in obsolete_columns:
                batch_op.drop_column(name)
        return
    for name in obsolete_columns:
        op.drop_column("ui_tool_definitions", name)


def downgrade() -> None:
    bind = op.get_bind()
    columns = []
    if not has_column(bind, "ui_tool_definitions", "mcp_server_name"):
        columns.append(sa.Column("mcp_server_name", key_type(bind), nullable=True))
    if not has_column(bind, "ui_tool_definitions", "override"):
        columns.append(
            sa.Column(
                "override",
                sa.Boolean(),
                server_default=boolean_default(bind, value=False),
                nullable=False,
            )
        )
    if not has_column(bind, "ui_tool_definitions", "discovered_at"):
        columns.append(sa.Column("discovered_at", sa.DateTime(timezone=True), nullable=True))

    if columns:
        if is_sqlite(bind):
            with op.batch_alter_table("ui_tool_definitions") as batch_op:
                for column in columns:
                    batch_op.add_column(column)
        else:
            for column in columns:
                op.add_column("ui_tool_definitions", column)

    index_name = "ix_ui_tool_definitions_mcp_server"
    if has_column(bind, "ui_tool_definitions", "mcp_server_name") and not _has_index(
        bind,
        "ui_tool_definitions",
        index_name,
    ):
        op.create_index(index_name, "ui_tool_definitions", ["mcp_server_name"])

    if has_column(bind, "mcp_servers", "resource_capabilities"):
        op.drop_column("mcp_servers", "resource_capabilities")
