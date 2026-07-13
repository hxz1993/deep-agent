"""auxiliary_model_configs table

Revision ID: 015
Revises: 014
Create Date: 2026-07-10

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import (
    constraint_exists,
    create_foreign_key,
    has_table,
    insert_or_ignore,
    is_sqlite,
)

revision: str = "015"
down_revision: Union[str, Sequence[str], None] = "014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AGENT_ID_LENGTH = 36
TABLE_NAME = "auxiliary_model_configs"
FK_NAME = "auxiliary_model_configs_llm_model_id_fkey"
TITLE_USE_CASE = "title"


def _bind():
    return op.get_bind()


def _auxiliary_model_configs_table() -> sa.TableClause:
    return sa.table(
        TABLE_NAME,
        sa.column("use_case", sa.String(64)),
        sa.column("llm_model_id", sa.String(AGENT_ID_LENGTH)),
    )


def _seed_title_row(bind) -> None:
    insert_or_ignore(
        bind,
        _auxiliary_model_configs_table(),
        {"use_case": TITLE_USE_CASE, "llm_model_id": None},
        conflict_columns=["use_case"],
    )


def upgrade() -> None:
    bind = _bind()
    if not has_table(bind, TABLE_NAME):
        op.create_table(
            TABLE_NAME,
            sa.Column("use_case", sa.String(64), nullable=False),
            sa.Column("llm_model_id", sa.String(AGENT_ID_LENGTH), nullable=True),
            sa.Column(
                "created_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.Column(
                "updated_at",
                sa.DateTime(timezone=True),
                server_default=sa.text("CURRENT_TIMESTAMP"),
                nullable=False,
            ),
            sa.PrimaryKeyConstraint("use_case"),
        )

    if is_sqlite(bind):
        if not constraint_exists(bind, TABLE_NAME, FK_NAME):
            with op.batch_alter_table(TABLE_NAME) as batch_op:
                batch_op.create_foreign_key(
                    FK_NAME,
                    "llm_models",
                    ["llm_model_id"],
                    ["id"],
                    ondelete="SET NULL",
                )
    elif not constraint_exists(bind, TABLE_NAME, FK_NAME):
        create_foreign_key(
            bind,
            FK_NAME,
            TABLE_NAME,
            "llm_models",
            ["llm_model_id"],
            ["id"],
            ondelete="SET NULL",
        )

    _seed_title_row(bind)


def downgrade() -> None:
    bind = _bind()
    if has_table(bind, TABLE_NAME):
        op.drop_table(TABLE_NAME)
