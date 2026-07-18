"""chat_sessions user ownership index

Revision ID: 017
Revises: 016
Create Date: 2026-07-18

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "017"
down_revision: Union[str, Sequence[str], None] = "016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _alter_user_id(
    column_type: sa.types.TypeEngine,
    *,
    existing_type: sa.types.TypeEngine,
) -> None:
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        with op.batch_alter_table("chat_sessions") as batch_op:
            batch_op.alter_column(
                "user_id",
                existing_type=existing_type,
                type_=column_type,
                existing_nullable=True,
            )
        return
    op.alter_column(
        "chat_sessions",
        "user_id",
        existing_type=existing_type,
        type_=column_type,
        existing_nullable=True,
    )


def upgrade() -> None:
    _alter_user_id(sa.String(length=128), existing_type=sa.Text())
    op.create_index(
        "ix_chat_sessions_user_id_agent_id_updated_at",
        "chat_sessions",
        ["user_id", "agent_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_chat_sessions_user_id_agent_id_updated_at",
        table_name="chat_sessions",
    )
    _alter_user_id(sa.Text(), existing_type=sa.String(length=128))
