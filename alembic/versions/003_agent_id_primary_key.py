"""agents id primary key and chat_sessions agent_id

Revision ID: 003
Revises: 002
Create Date: 2026-06-05

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from deep_agent.db.migration_dialect import (
    has_column,
    is_postgresql,
    is_sqlite,
    random_agent_id_expr,
    rebuild_agents_primary_key_sqlite,
)
from sqlalchemy.dialects import postgresql

revision: str = "003"
down_revision: Union[str, Sequence[str], None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgresql() -> bool:
    return is_postgresql(op.get_bind())


def _uuid_type():
    if _is_postgresql():
        return postgresql.UUID(as_uuid=True)
    return sa.Uuid()


def _uuid_server_default():
    if _is_postgresql():
        return sa.text("gen_random_uuid()")
    return None


def _has_column(table_name: str, column_name: str) -> bool:
    return has_column(op.get_bind(), table_name, column_name)


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


def _drop_fk_sqlite(table_name: str, constraint_name: str) -> None:
    with op.batch_alter_table(table_name) as batch_op:
        batch_op.drop_constraint(constraint_name, type_="foreignkey")


def _upgrade_sqlite() -> None:
    bind = op.get_bind()
    id_expr = random_agent_id_expr(bind)
    id_type = sa.String(36)

    if not _has_column("agents", "id"):
        op.add_column("agents", sa.Column("id", id_type, nullable=True))
    op.execute(sa.text(f"UPDATE agents SET id = {id_expr} WHERE id IS NULL"))

    if not _has_column("chat_sessions", "agent_id"):
        op.add_column("chat_sessions", sa.Column("agent_id", id_type, nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions
            SET agent_id = (
                SELECT a.id FROM agents a WHERE a.name = chat_sessions.agent_name
            )
            """
        )
    )

    if not _has_column("chat_sessions", "new_session_key"):
        op.add_column("chat_sessions", sa.Column("new_session_key", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions
            SET new_session_key = (
                SELECT a.id || ':' || SUBSTR(chat_sessions.session_key, INSTR(chat_sessions.session_key, ':') + 1)
                FROM agents a
                WHERE a.name = chat_sessions.agent_name
                  AND INSTR(chat_sessions.session_key, ':') > 0
                  AND SUBSTR(chat_sessions.session_key, 1, INSTR(chat_sessions.session_key, ':') - 1) = a.name
            )
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

    _drop_fk_sqlite("chat_messages", "chat_messages_session_key_fkey")
    op.execute(
        sa.text(
            """
            UPDATE chat_messages
            SET session_key = (
                SELECT cs.new_session_key
                FROM chat_sessions cs
                WHERE cs.session_key = chat_messages.session_key
                  AND cs.new_session_key IS NOT NULL
                  AND cs.new_session_key <> cs.session_key
            )
            WHERE session_key IN (
                SELECT cs.session_key
                FROM chat_sessions cs
                WHERE cs.new_session_key IS NOT NULL
                  AND cs.new_session_key <> cs.session_key
            )
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
    with op.batch_alter_table("chat_sessions") as batch_op:
        batch_op.drop_column("new_session_key")
    with op.batch_alter_table("chat_messages") as batch_op:
        batch_op.create_foreign_key(
            "chat_messages_session_key_fkey",
            "chat_sessions",
            ["session_key"],
            ["session_key"],
            ondelete="CASCADE",
        )

    _drop_fk_sqlite("chat_sessions", "chat_sessions_agent_name_fkey")
    with op.batch_alter_table("chat_sessions") as batch_op:
        batch_op.drop_column("agent_name")

    rebuild_agents_primary_key_sqlite(bind, pk_column="id")

    with op.batch_alter_table("chat_sessions") as batch_op:
        batch_op.create_foreign_key(
            "chat_sessions_agent_id_fkey",
            "agents",
            ["agent_id"],
            ["id"],
            ondelete="SET NULL",
        )


def _downgrade_sqlite() -> None:
    op.add_column("chat_sessions", sa.Column("agent_name", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions
            SET agent_name = (
                SELECT a.name FROM agents a WHERE a.id = chat_sessions.agent_id
            )
            """
        )
    )

    op.add_column("chat_sessions", sa.Column("new_session_key", sa.Text(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE chat_sessions
            SET new_session_key = (
                SELECT a.name || ':' || SUBSTR(chat_sessions.session_key, INSTR(chat_sessions.session_key, ':') + 1)
                FROM agents a
                WHERE a.id = chat_sessions.agent_id
                  AND INSTR(chat_sessions.session_key, ':') > 0
                  AND SUBSTR(chat_sessions.session_key, 1, INSTR(chat_sessions.session_key, ':') - 1) = a.id
            )
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
            UPDATE chat_messages
            SET session_key = (
                SELECT cs.new_session_key
                FROM chat_sessions cs
                WHERE cs.session_key = chat_messages.session_key
                  AND cs.new_session_key IS NOT NULL
                  AND cs.new_session_key <> cs.session_key
            )
            WHERE session_key IN (
                SELECT cs.session_key
                FROM chat_sessions cs
                WHERE cs.new_session_key IS NOT NULL
                  AND cs.new_session_key <> cs.session_key
            )
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

    rebuild_agents_primary_key_sqlite(bind, pk_column="name")

    op.create_foreign_key(
        "chat_sessions_agent_name_fkey",
        "chat_sessions",
        "agents",
        ["agent_name"],
        ["name"],
        ondelete="SET NULL",
    )
    op.drop_column("agents", "id")


def upgrade() -> None:
    bind = op.get_bind()
    if is_postgresql(bind):
        _upgrade_postgresql()
    elif is_sqlite(bind):
        _upgrade_sqlite()
    else:
        _upgrade_mysql()


def downgrade() -> None:
    bind = op.get_bind()
    if is_postgresql(bind):
        _downgrade_postgresql()
    elif is_sqlite(bind):
        _downgrade_sqlite()
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
