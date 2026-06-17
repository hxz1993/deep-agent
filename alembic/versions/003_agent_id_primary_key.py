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


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _uuid_type():
    if _is_postgresql():
        return postgresql.UUID(as_uuid=True)
    return sa.Uuid()


def _uuid_server_default():
    if _is_postgresql():
        return sa.text("gen_random_uuid()")
    return None


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    result = bind.execute(
        sa.text(
            """
            SELECT COUNT(*)
            FROM information_schema.columns
            WHERE table_schema = DATABASE()
              AND table_name = :table_name
              AND column_name = :column_name
            """
        ),
        {"table_name": table_name, "column_name": column_name},
    )
    return int(result.scalar_one()) > 0


def _upgrade_postgresql() -> None:
    op.add_column(
        "agents",
        sa.Column("id", _uuid_type(), server_default=_uuid_server_default(), nullable=True),
    )
    op.execute(sa.text("UPDATE agents SET id = gen_random_uuid() WHERE id IS NULL"))
    op.alter_column("agents", "id", existing_type=_uuid_type(), nullable=False)

    op.add_column(
        "chat_sessions",
        sa.Column("agent_id", _uuid_type(), nullable=True),
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


def _upgrade_mysql() -> None:
    if not _has_column("agents", "id"):
        op.add_column(
            "agents",
            sa.Column("id", _uuid_type(), server_default=_uuid_server_default(), nullable=True),
        )
    op.execute(sa.text("UPDATE agents SET id = REPLACE(UUID(), '-', '') WHERE id IS NULL"))
    op.alter_column("agents", "id", existing_type=_uuid_type(), nullable=False)

    if not _has_column("chat_sessions", "agent_id"):
        op.add_column(
            "chat_sessions",
            sa.Column("agent_id", _uuid_type(), nullable=True),
        )
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions cs
            INNER JOIN agents a ON cs.agent_name = a.name
            SET cs.agent_id = a.id
            """
        )
    )

    if not _has_column("chat_sessions", "new_session_key"):
        op.add_column("chat_sessions", sa.Column("new_session_key", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions cs
            INNER JOIN agents a ON cs.agent_name = a.name
            SET cs.new_session_key = CONCAT(CAST(a.id AS CHAR), ':', SUBSTRING_INDEX(cs.session_key, ':', -1))
            WHERE INSTR(cs.session_key, ':') > 0
              AND SUBSTRING_INDEX(cs.session_key, ':', 1) = a.name
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
            INNER JOIN chat_sessions cs ON cm.session_key = cs.session_key
            SET cm.session_key = cs.new_session_key
            WHERE cs.new_session_key IS NOT NULL
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

    op.drop_constraint("PRIMARY", "agents", type_="primary")
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


def upgrade() -> None:
    if _is_postgresql():
        _upgrade_postgresql()
    else:
        _upgrade_mysql()


def downgrade() -> None:
    if _is_postgresql():
        _downgrade_postgresql()
    else:
        _downgrade_mysql()


def _downgrade_postgresql() -> None:
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


def _downgrade_mysql() -> None:
    op.add_column("chat_sessions", sa.Column("agent_name", sa.Text(), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE chat_sessions cs
            INNER JOIN agents a ON cs.agent_id = a.id
            SET cs.agent_name = a.name
            """
        )
    )

    op.add_column("chat_sessions", sa.Column("new_session_key", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions cs
            INNER JOIN agents a ON cs.agent_id = a.id
            SET cs.new_session_key = CONCAT(a.name, ':', SUBSTRING_INDEX(cs.session_key, ':', -1))
            WHERE INSTR(cs.session_key, ':') > 0
              AND SUBSTRING_INDEX(cs.session_key, ':', 1) = CAST(a.id AS CHAR)
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
            INNER JOIN chat_sessions cs ON cm.session_key = cs.session_key
            SET cm.session_key = cs.new_session_key
            WHERE cs.new_session_key IS NOT NULL
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
    op.create_primary_key("PRIMARY", "agents", ["name"])

    op.create_foreign_key(
        "chat_sessions_agent_name_fkey",
        "chat_sessions",
        "agents",
        ["agent_name"],
        ["name"],
        ondelete="SET NULL",
    )

    op.drop_column("agents", "id")
