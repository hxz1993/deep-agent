"""chat_sessions session_id and updated_at indexes

Revision ID: 012
Revises: 011
Create Date: 2026-06-24

"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op

revision: str = "012"
down_revision: Union[str, Sequence[str], None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index("ix_chat_sessions_session_id", "chat_sessions", ["session_id"])
    op.create_index("ix_chat_sessions_updated_at", "chat_sessions", ["updated_at"])
    op.create_index(
        "ix_chat_sessions_agent_id_updated_at",
        "chat_sessions",
        ["agent_id", "updated_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_chat_sessions_agent_id_updated_at", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_updated_at", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_session_id", table_name="chat_sessions")
