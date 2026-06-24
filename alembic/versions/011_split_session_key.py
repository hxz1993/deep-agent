"""Split session_key into agent_id + session_id composite keys

Revision ID: 011
Revises: 010
Create Date: 2026-06-24

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "011"
down_revision: Union[str, Sequence[str], None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AGENT_ID_LENGTH = 36
NO_AGENT_SCOPE = ""

_KNOWN_FOREIGN_KEYS: tuple[tuple[str, str], ...] = (
    ("chat_messages", "chat_messages_session_key_fkey"),
    ("chat_messages", "chat_messages_session_fkey"),
    ("chat_sessions", "chat_sessions_agent_id_fkey"),
    ("chat_sessions", "chat_sessions_agent_name_fkey"),
    ("ui_tool_definitions", "ui_tool_definitions_mcp_server_name_fkey"),
)


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _is_postgresql() -> bool:
    return _dialect_name() == "postgresql"


def _key_type():
    if _is_postgresql():
        return sa.Text()
    return sa.String(191)


def _has_column(table_name: str, column_name: str) -> bool:
    bind = op.get_bind()
    if _is_postgresql():
        result = bind.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM information_schema.columns
                WHERE table_schema = current_schema()
                  AND table_name = :table_name
                  AND column_name = :column_name
                """
            ),
            {"table_name": table_name, "column_name": column_name},
        )
    else:
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
    return bool(result.scalar_one())


def _constraint_exists(table_name: str, constraint_name: str) -> bool:
    bind = op.get_bind()
    if _is_postgresql():
        result = bind.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM information_schema.table_constraints
                WHERE table_schema = current_schema()
                  AND table_name = :table_name
                  AND constraint_name = :constraint_name
                """
            ),
            {"table_name": table_name, "constraint_name": constraint_name},
        )
    else:
        result = bind.execute(
            sa.text(
                """
                SELECT COUNT(*)
                FROM information_schema.table_constraints
                WHERE table_schema = DATABASE()
                  AND table_name = :table_name
                  AND constraint_name = :constraint_name
                """
            ),
            {"table_name": table_name, "constraint_name": constraint_name},
        )
    return bool(result.scalar_one())


def _drop_constraint_if_exists(
    table_name: str,
    constraint_name: str,
    *,
    constraint_type: str,
) -> None:
    if _constraint_exists(table_name, constraint_name):
        op.drop_constraint(constraint_name, table_name, type_=constraint_type)


def _drop_known_foreign_keys() -> None:
    for table_name, constraint_name in _KNOWN_FOREIGN_KEYS:
        _drop_constraint_if_exists(table_name, constraint_name, constraint_type="foreignkey")


def _session_id_expr(column: str = "session_key") -> str:
    if _is_postgresql():
        return (
            f"CASE WHEN position(':' in {column}) > 0 "
            f"THEN split_part({column}, ':', 2) ELSE {column} END"
        )
    return (
        f"CASE WHEN INSTR({column}, ':') > 0 "
        f"THEN SUBSTRING_INDEX({column}, ':', -1) ELSE {column} END"
    )


def _agent_prefix_expr(column: str = "session_key") -> str:
    if _is_postgresql():
        return (
            f"CASE WHEN position(':' in {column}) > 0 "
            f"THEN split_part({column}, ':', 1) ELSE '' END"
        )
    return (
        f"CASE WHEN INSTR({column}, ':') > 0 "
        f"THEN SUBSTRING_INDEX({column}, ':', 1) ELSE '' END"
    )


def _coalesced_agent_id_expr() -> str:
    prefix = _agent_prefix_expr()
    return f"COALESCE(NULLIF(TRIM(agent_id), ''), NULLIF({prefix}, ''), '')"


def _assert_no_duplicate_sessions(bind) -> None:
    rows = bind.execute(
        sa.text(
            """
            SELECT agent_id, session_id, COUNT(*) AS row_count
            FROM chat_sessions
            GROUP BY agent_id, session_id
            HAVING COUNT(*) > 1
            """
        )
    ).fetchall()
    if rows:
        sample = ", ".join(f"({agent_id!r}, {session_id!r})" for agent_id, session_id, _ in rows[:5])
        raise RuntimeError(
            "chat_sessions migration produced duplicate (agent_id, session_id) keys: "
            f"{sample}. Merge or delete conflicting rows before re-running."
        )


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect not in {"postgresql", "mysql"}:
        return

    if not _has_column("chat_sessions", "session_key"):
        _drop_known_foreign_keys()
        return

    bind = op.get_bind()
    session_id_expr = _session_id_expr()
    agent_id_expr = _coalesced_agent_id_expr()

    if not _has_column("chat_sessions", "session_id"):
        op.add_column("chat_sessions", sa.Column("session_id", _key_type(), nullable=True))
    if not _has_column("chat_messages", "agent_id"):
        op.add_column(
            "chat_messages",
            sa.Column("agent_id", sa.String(AGENT_ID_LENGTH), nullable=True),
        )
    if not _has_column("chat_messages", "session_id"):
        op.add_column("chat_messages", sa.Column("session_id", _key_type(), nullable=True))

    bind.execute(
        sa.text(
            f"""
            UPDATE chat_sessions
            SET session_id = {session_id_expr},
                agent_id = {agent_id_expr}
            """
        )
    )
    if _is_postgresql():
        bind.execute(
            sa.text(
                """
                UPDATE chat_messages AS cm
                SET agent_id = cs.agent_id,
                    session_id = cs.session_id
                FROM chat_sessions AS cs
                WHERE cm.session_key = cs.session_key
                """
            )
        )
    else:
        bind.execute(
            sa.text(
                """
                UPDATE chat_messages AS cm
                INNER JOIN chat_sessions AS cs ON cm.session_key = cs.session_key
                SET cm.agent_id = cs.agent_id,
                    cm.session_id = cs.session_id
                """
            )
        )

    _assert_no_duplicate_sessions(bind)

    _drop_known_foreign_keys()

    _drop_constraint_if_exists("chat_messages", "uq_chat_messages_session_sequence", constraint_type="unique")
    _drop_constraint_if_exists("chat_sessions", "chat_sessions_pkey", constraint_type="primary")

    op.alter_column("chat_sessions", "session_id", nullable=False)
    op.alter_column("chat_sessions", "agent_id", nullable=False, server_default=NO_AGENT_SCOPE)
    op.alter_column("chat_messages", "agent_id", nullable=False, server_default=NO_AGENT_SCOPE)
    op.alter_column("chat_messages", "session_id", nullable=False)

    if _has_column("chat_sessions", "session_key"):
        op.drop_column("chat_sessions", "session_key")
    if _has_column("chat_messages", "session_key"):
        op.drop_column("chat_messages", "session_key")

    if not _constraint_exists("chat_sessions", "chat_sessions_pkey"):
        op.create_primary_key(
            "chat_sessions_pkey",
            "chat_sessions",
            ["agent_id", "session_id"],
        )
    _drop_constraint_if_exists("chat_messages", "uq_chat_messages_session_sequence", constraint_type="unique")
    if not _constraint_exists("chat_messages", "uq_chat_messages_session_sequence"):
        op.create_unique_constraint(
            "uq_chat_messages_session_sequence",
            "chat_messages",
            ["agent_id", "session_id", "sequence"],
        )


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect not in {"postgresql", "mysql"}:
        return

    bind = op.get_bind()

    if not _has_column("chat_sessions", "session_id"):
        return

    _drop_known_foreign_keys()
    _drop_constraint_if_exists("chat_messages", "uq_chat_messages_session_sequence", constraint_type="unique")
    _drop_constraint_if_exists("chat_sessions", "chat_sessions_pkey", constraint_type="primary")

    if not _has_column("chat_sessions", "session_key"):
        op.add_column("chat_sessions", sa.Column("session_key", _key_type(), nullable=True))
    if not _has_column("chat_messages", "session_key"):
        op.add_column("chat_messages", sa.Column("session_key", _key_type(), nullable=True))

    if _is_postgresql():
        bind.execute(
            sa.text(
                """
                UPDATE chat_sessions
                SET session_key = CASE
                    WHEN agent_id = '' THEN session_id
                    ELSE agent_id || ':' || session_id
                END
                """
            )
        )
        bind.execute(
            sa.text(
                """
                UPDATE chat_messages AS cm
                SET session_key = CASE
                    WHEN cm.agent_id = '' THEN cm.session_id
                    ELSE cm.agent_id || ':' || cm.session_id
                END
                """
            )
        )
    else:
        bind.execute(
            sa.text(
                """
                UPDATE chat_sessions
                SET session_key = CASE
                    WHEN agent_id = '' THEN session_id
                    ELSE CONCAT(agent_id, ':', session_id)
                END
                """
            )
        )
        bind.execute(
            sa.text(
                """
                UPDATE chat_messages
                SET session_key = CASE
                    WHEN agent_id = '' THEN session_id
                    ELSE CONCAT(agent_id, ':', session_id)
                END
                """
            )
        )

    op.alter_column("chat_sessions", "session_key", nullable=False)
    op.alter_column("chat_messages", "session_key", nullable=False)

    op.drop_column("chat_sessions", "session_id")
    op.drop_column("chat_messages", "agent_id")
    op.drop_column("chat_messages", "session_id")

    bind.execute(sa.text("UPDATE chat_sessions SET agent_id = NULL WHERE agent_id = ''"))

    op.create_primary_key("chat_sessions_pkey", "chat_sessions", ["session_key"])
    op.create_unique_constraint(
        "uq_chat_messages_session_sequence",
        "chat_messages",
        ["session_key", "sequence"],
    )
