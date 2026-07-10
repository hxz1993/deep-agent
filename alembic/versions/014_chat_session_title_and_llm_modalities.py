"""chat_sessions.title + llm_models modality metadata

Revision ID: 014
Revises: 013
Create Date: 2026-07-09

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import has_column, has_table, is_sqlite, text_json_type

revision: str = "014"
down_revision: Union[str, Sequence[str], None] = "013"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()

    if has_table(bind, "chat_sessions") and not has_column(bind, "chat_sessions", "title"):
        if is_sqlite(bind):
            with op.batch_alter_table("chat_sessions") as batch_op:
                batch_op.add_column(sa.Column("title", sa.Text(), nullable=True))
        else:
            op.add_column("chat_sessions", sa.Column("title", sa.Text(), nullable=True))

    if not has_table(bind, "llm_models"):
        return

    modalities_type = text_json_type(bind)

    if not has_column(bind, "llm_models", "input_modalities"):
        if is_sqlite(bind):
            with op.batch_alter_table("llm_models") as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "input_modalities",
                        modalities_type,
                        nullable=True,
                    )
                )
        else:
            op.add_column(
                "llm_models",
                sa.Column(
                    "input_modalities",
                    modalities_type,
                    nullable=True,
                ),
            )

    if not has_column(bind, "llm_models", "capabilities_source"):
        if is_sqlite(bind):
            with op.batch_alter_table("llm_models") as batch_op:
                batch_op.add_column(
                    sa.Column("capabilities_source", sa.String(32), nullable=True)
                )
        else:
            op.add_column(
                "llm_models",
                sa.Column("capabilities_source", sa.String(32), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()

    if has_table(bind, "chat_sessions") and has_column(bind, "chat_sessions", "title"):
        if is_sqlite(bind):
            with op.batch_alter_table("chat_sessions") as batch_op:
                batch_op.drop_column("title")
        else:
            op.drop_column("chat_sessions", "title")

    if not has_table(bind, "llm_models"):
        return

    if has_column(bind, "llm_models", "capabilities_source"):
        if is_sqlite(bind):
            with op.batch_alter_table("llm_models") as batch_op:
                batch_op.drop_column("capabilities_source")
        else:
            op.drop_column("llm_models", "capabilities_source")

    if has_column(bind, "llm_models", "input_modalities"):
        if is_sqlite(bind):
            with op.batch_alter_table("llm_models") as batch_op:
                batch_op.drop_column("input_modalities")
        else:
            op.drop_column("llm_models", "input_modalities")
