"""context compaction checkpoints

Revision ID: 023
Revises: 022
Create Date: 2026-07-29

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import has_table, now_default, text_json_type


revision: str = "023"
down_revision: Union[str, Sequence[str], None] = "022"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if has_table(bind, "chat_context_checkpoints"):
        return
    op.create_table(
        "chat_context_checkpoints",
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(191), nullable=False),
        sa.Column("source_last_sequence", sa.Integer(), nullable=False),
        sa.Column("source_history_hash", sa.String(64), nullable=False),
        sa.Column("payload", text_json_type(bind), nullable=False),
        sa.Column("payload_schema_version", sa.Integer(), nullable=False),
        sa.Column("projection_fingerprint", sa.String(64), nullable=False),
        sa.Column("estimated_tokens", sa.BigInteger(), nullable=False),
        sa.Column("summary_model_id", sa.String(36), nullable=True),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            nullable=False,
            server_default=sa.text("1"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default(bind),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default(bind),
        ),
        sa.PrimaryKeyConstraint("agent_id", "session_id"),
    )


def downgrade() -> None:
    bind = op.get_bind()
    if has_table(bind, "chat_context_checkpoints"):
        op.drop_table("chat_context_checkpoints")
