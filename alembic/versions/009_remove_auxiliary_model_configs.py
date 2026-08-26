"""Remove the obsolete auxiliary model configuration table.

Revision ID: 009
Revises: 008
Create Date: 2026-08-26

Revision 008 existed in development with two physical variants: older databases
still have ``auxiliary_model_configs`` while newer local databases may already
have removed it.  The guarded DDL makes both variants converge on one Revision
009 schema without rewriting Alembic metadata by hand.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import now_default


revision: str = "009"
down_revision: Union[str, Sequence[str], None] = "008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AGENT_ID_LENGTH = 36
TABLE_NAME = "auxiliary_model_configs"


def _bind():
    return op.get_bind()


def _now_default():
    return now_default(_bind())


def _has_table() -> bool:
    return sa.inspect(_bind()).has_table(TABLE_NAME)


def upgrade() -> None:
    if _has_table():
        op.drop_table(TABLE_NAME)


def downgrade() -> None:
    if _has_table():
        return
    op.create_table(
        TABLE_NAME,
        sa.Column("use_case", sa.String(64), nullable=False),
        sa.Column("llm_model_id", sa.String(AGENT_ID_LENGTH), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["llm_model_id"],
            ["llm_models.id"],
            name="auxiliary_model_configs_llm_model_id_fkey",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("use_case"),
    )
