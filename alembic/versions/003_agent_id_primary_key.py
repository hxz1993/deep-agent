"""agents id primary key and chat_sessions agent_id

Revision ID: 003
Revises: 002
Create Date: 2026-06-05

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, Sequence[str], None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "agents",
        sa.Column("id", postgresql.UUID(as_uuid=True), server_default=sa.text("gen_random_uuid()"), nullable=True),
    )
    op.execute(sa.text("UPDATE agents SET id = gen_random_uuid() WHERE id IS NULL"))
    op.alter_column("agents", "id", nullable=False)

    op.add_column(
        "chat_sessions",
        sa.Column("agent_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions cs
            SET agent_id = a.id
            FROM agents a
            WHERE cs.agent_name = a.name
            """
        )
    )

    op.add_column("chat_sessions", sa.Column("new_session_key", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions cs
            SET new_session_key = a.id::text || ':' || split_part(cs.session_key, ':', 2)
            FROM agents a
            WHERE cs.agent_name = a.name
              AND position(':' in cs.session_key) > 0
              AND split_part(cs.session_key, ':', 1) = a.name
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions
            SET new_session_key = session_key
            WHERE new_session_key IS NULL
            """
        )
    )

    op.drop_constraint("chat_messages_session_key_fkey", "chat_messages", type_="foreignkey")
    op.execute(
        sa.text(
            """
            UPDATE chat_messages cm
            SET session_key = cs.new_session_key
            FROM chat_sessions cs
            WHERE cm.session_key = cs.session_key
              AND cs.new_session_key IS NOT NULL
              AND cs.new_session_key <> cs.session_key
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions
            SET session_key = new_session_key
            WHERE new_session_key IS NOT NULL
            """
        )
    )
    op.drop_column("chat_sessions", "new_session_key")
    op.create_foreign_key(
        "chat_messages_session_key_fkey",
        "chat_messages",
        "chat_sessions",
        ["session_key"],
        ["session_key"],
        ondelete="CASCADE",
    )

    op.drop_constraint("chat_sessions_agent_name_fkey", "chat_sessions", type_="foreignkey")
    op.drop_column("chat_sessions", "agent_name")

    op.drop_constraint("agents_pkey", "agents", type_="primary")
    op.create_primary_key("agents_pkey", "agents", ["id"])
    op.create_unique_constraint("uq_agents_name", "agents", ["name"])

    op.create_foreign_key(
        "chat_sessions_agent_id_fkey",
        "chat_sessions",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.add_column("chat_sessions", sa.Column("agent_name", sa.Text(), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE chat_sessions cs
            SET agent_name = a.name
            FROM agents a
            WHERE cs.agent_id = a.id
            """
        )
    )

    op.add_column("chat_sessions", sa.Column("new_session_key", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions cs
            SET new_session_key = a.name || ':' || split_part(cs.session_key, ':', 2)
            FROM agents a
            WHERE cs.agent_id = a.id
              AND position(':' in cs.session_key) > 0
              AND split_part(cs.session_key, ':', 1) = a.id::text
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions
            SET new_session_key = session_key
            WHERE new_session_key IS NULL
            """
        )
    )

    op.drop_constraint("chat_messages_session_key_fkey", "chat_messages", type_="foreignkey")
    op.execute(
        sa.text(
            """
            UPDATE chat_messages cm
            SET session_key = cs.new_session_key
            FROM chat_sessions cs
            WHERE cm.session_key = cs.session_key
              AND cs.new_session_key IS NOT NULL
              AND cs.new_session_key <> cs.session_key
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions
            SET session_key = new_session_key
            WHERE new_session_key IS NOT NULL
            """
        )
    )
    op.drop_column("chat_sessions", "new_session_key")
    op.create_foreign_key(
        "chat_messages_session_key_fkey",
        "chat_messages",
        "chat_sessions",
        ["session_key"],
        ["session_key"],
        ondelete="CASCADE",
    )

    op.drop_constraint("chat_sessions_agent_id_fkey", "chat_sessions", type_="foreignkey")
    op.drop_column("chat_sessions", "agent_id")

    op.drop_constraint("agents_pkey", "agents", type_="primary")
    op.drop_constraint("uq_agents_name", "agents", type_="unique")
    op.create_primary_key("agents_pkey", "agents", ["name"])

    op.create_foreign_key(
        "chat_sessions_agent_name_fkey",
        "chat_sessions",
        "agents",
        ["agent_name"],
        ["name"],
        ondelete="SET NULL",
    )

    op.drop_column("agents", "id")
