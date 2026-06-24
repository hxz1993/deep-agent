"""Split session_key into agent_id + session_id composite keys

Revision ID: 011
Revises: 010
Create Date: 2026-06-24

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from deep_agent.db.migration_dialect import (
    agent_prefix_from_key_expr,
    constraint_exists,
    has_column,
    is_postgresql,
    is_sqlite,
    key_type,
    session_id_from_key_expr,
)

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


def _bind():
    return op.get_bind()


def _has_column(table_name: str, column_name: str) -> bool:
    return has_column(_bind(), table_name, column_name)


def _constraint_exists(table_name: str, constraint_name: str) -> bool:
    return constraint_exists(_bind(), table_name, constraint_name)


def _drop_constraint_if_exists(
    table_name: str,
    constraint_name: str,
    *,
    constraint_type: str,
) -> None:
    if not _constraint_exists(table_name, constraint_name):
        return
    bind = _bind()
    if is_sqlite(bind):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_constraint(constraint_name, type_=constraint_type)
        return
    op.drop_constraint(constraint_name, table_name, type_=constraint_type)


def _drop_known_foreign_keys() -> None:
    for table_name, constraint_name in _KNOWN_FOREIGN_KEYS:
        _drop_constraint_if_exists(table_name, constraint_name, constraint_type="foreignkey")


def _coalesced_agent_id_expr(bind) -> str:
    prefix = agent_prefix_from_key_expr(bind=bind)
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


def _sync_chat_messages_from_sessions(bind) -> None:
    if is_postgresql(bind):
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
        return
    if is_sqlite(bind):
        bind.execute(
            sa.text(
                """
                UPDATE chat_messages
                SET agent_id = (
                    SELECT cs.agent_id
                    FROM chat_sessions cs
                    WHERE cs.session_key = chat_messages.session_key
                ),
                session_id = (
                    SELECT cs.session_id
                    FROM chat_sessions cs
                    WHERE cs.session_key = chat_messages.session_key
                )
                """
            )
        )
        return
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


def _drop_chat_sessions_primary_key() -> None:
    bind = _bind()
    if is_sqlite(bind):
        return
    _drop_constraint_if_exists("chat_sessions", "chat_sessions_pkey", constraint_type="primary")


def _create_chat_sessions_primary_key() -> None:
    bind = _bind()
    if not _constraint_exists("chat_sessions", "chat_sessions_pkey"):
        if is_sqlite(bind):
            with op.batch_alter_table("chat_sessions") as batch_op:
                batch_op.create_primary_key("chat_sessions_pkey", ["agent_id", "session_id"])
            return
        op.create_primary_key(
            "chat_sessions_pkey",
            "chat_sessions",
            ["agent_id", "session_id"],
        )


def _alter_not_null(
    table: str,
    column: str,
    *,
    column_type,
    bind,
    server_default: str | None = None,
) -> None:
    alter_kwargs: dict = {"nullable": False}
    if server_default is not None:
        alter_kwargs["server_default"] = server_default
    if is_sqlite(bind):
        with op.batch_alter_table(table) as batch_op:
            batch_op.alter_column(column, **alter_kwargs)
        return
    # ponytail: MySQL MODIFY COLUMN requires existing_type even when only changing nullability.
    op.alter_column(
        table,
        column,
        existing_type=column_type,
        existing_nullable=True,
        **alter_kwargs,
    )


def upgrade() -> None:
    if not _has_column("chat_sessions", "session_key"):
        _drop_known_foreign_keys()
        return

    bind = _bind()
    session_id_expr = session_id_from_key_expr(bind=bind)
    agent_id_expr = _coalesced_agent_id_expr(bind)

    if not _has_column("chat_sessions", "session_id"):
        op.add_column("chat_sessions", sa.Column("session_id", key_type(bind), nullable=True))
    if not _has_column("chat_messages", "agent_id"):
        op.add_column(
            "chat_messages",
            sa.Column("agent_id", sa.String(AGENT_ID_LENGTH), nullable=True),
        )
    if not _has_column("chat_messages", "session_id"):
        op.add_column("chat_messages", sa.Column("session_id", key_type(bind), nullable=True))

    bind.execute(
        sa.text(
            f"""
            UPDATE chat_sessions
            SET session_id = {session_id_expr},
                agent_id = {agent_id_expr}
            """
        )
    )
    _sync_chat_messages_from_sessions(bind)
    _assert_no_duplicate_sessions(bind)

    _drop_known_foreign_keys()
    _drop_constraint_if_exists("chat_messages", "uq_chat_messages_session_sequence", constraint_type="unique")
    _drop_chat_sessions_primary_key()

    drop_session_key_cs = _has_column("chat_sessions", "session_key")
    drop_session_key_cm = _has_column("chat_messages", "session_key")

    session_col_type = key_type(bind)
    agent_col_type = sa.String(AGENT_ID_LENGTH)

    _alter_not_null("chat_sessions", "session_id", column_type=session_col_type, bind=bind)
    _alter_not_null(
        "chat_sessions",
        "agent_id",
        column_type=agent_col_type,
        bind=bind,
        server_default=NO_AGENT_SCOPE,
    )
    _alter_not_null(
        "chat_messages",
        "agent_id",
        column_type=agent_col_type,
        bind=bind,
        server_default=NO_AGENT_SCOPE,
    )
    _alter_not_null("chat_messages", "session_id", column_type=session_col_type, bind=bind)

    if drop_session_key_cs:
        op.drop_column("chat_sessions", "session_key")
    if drop_session_key_cm:
        op.drop_column("chat_messages", "session_key")

    _create_chat_sessions_primary_key()
    _drop_constraint_if_exists("chat_messages", "uq_chat_messages_session_sequence", constraint_type="unique")
    if not _constraint_exists("chat_messages", "uq_chat_messages_session_sequence"):
        if is_sqlite(bind):
            with op.batch_alter_table("chat_messages") as batch_op:
                batch_op.create_unique_constraint(
                    "uq_chat_messages_session_sequence",
                    ["agent_id", "session_id", "sequence"],
                )
        else:
            op.create_unique_constraint(
                "uq_chat_messages_session_sequence",
                "chat_messages",
                ["agent_id", "session_id", "sequence"],
            )


def downgrade() -> None:
    bind = _bind()

    if not _has_column("chat_sessions", "session_id"):
        return

    _drop_known_foreign_keys()
    _drop_constraint_if_exists("chat_messages", "uq_chat_messages_session_sequence", constraint_type="unique")
    _drop_chat_sessions_primary_key()

    if not _has_column("chat_sessions", "session_key"):
        op.add_column("chat_sessions", sa.Column("session_key", key_type(bind), nullable=True))
    if not _has_column("chat_messages", "session_key"):
        op.add_column("chat_messages", sa.Column("session_key", key_type(bind), nullable=True))

    if is_postgresql(bind) or is_sqlite(bind):
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
                UPDATE chat_messages
                SET session_key = CASE
                    WHEN agent_id = '' THEN session_id
                    ELSE agent_id || ':' || session_id
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

    session_key_type = key_type(bind)
    _alter_not_null("chat_sessions", "session_key", column_type=session_key_type, bind=bind)
    _alter_not_null("chat_messages", "session_key", column_type=session_key_type, bind=bind)

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
