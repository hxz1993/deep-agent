"""Make Agent association the only resource activation switch.

Revision ID: 010
Revises: 009
Create Date: 2026-08-27

Configured Providers, models, Skills, MCP servers, UI tool packages and UI
tools are available resources.  Whether a Run receives tools and Skills is
determined exclusively by the Agent's ``skills`` and ``capabilities`` lists;
the Agent's ``model_id`` selects its model.  Existing Agents whose legacy
``capabilities`` value was NULL are migrated to an explicit empty list so new
Agents no longer inherit hidden default packages.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "010"
down_revision: Union[str, Sequence[str], None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


RESOURCE_TABLES = (
    "llm_providers",
    "llm_models",
    "skill_packages",
    "mcp_servers",
    "ui_tool_packages",
    "ui_tool_definitions",
)


def upgrade() -> None:
    op.execute(sa.text("UPDATE agents SET capabilities = '[]' WHERE capabilities IS NULL"))
    with op.batch_alter_table("agents") as batch:
        batch.alter_column(
            "capabilities",
            existing_type=sa.Text(),
            nullable=False,
        )
    # Revision 001 indexed ``source + enabled``.  SQLite's batch table
    # recreation otherwise tries to copy that legacy index after ``enabled``
    # has been removed and fails with "no such column: enabled".
    op.drop_index(
        "ix_ui_tool_definitions_source_enabled",
        table_name="ui_tool_definitions",
    )
    for table_name in RESOURCE_TABLES:
        with op.batch_alter_table(table_name) as batch:
            batch.drop_column("enabled")


def downgrade() -> None:
    for table_name in RESOURCE_TABLES:
        with op.batch_alter_table(table_name) as batch:
            batch.add_column(
                sa.Column(
                    "enabled",
                    sa.Boolean(),
                    server_default=sa.true(),
                    nullable=False,
                )
            )
    op.create_index(
        "ix_ui_tool_definitions_source_enabled",
        "ui_tool_definitions",
        ["source", "enabled"],
    )
    with op.batch_alter_table("agents") as batch:
        batch.alter_column(
            "capabilities",
            existing_type=sa.Text(),
            nullable=True,
        )
