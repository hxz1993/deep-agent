"""Convert uuid columns to varchar for cross-database portability

Revision ID: 008
Revises: 007
Create Date: 2026-06-17

"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "008"
down_revision: Union[str, Sequence[str], None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AGENT_ID_LENGTH = 36


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _is_postgresql() -> bool:
    return _dialect_name() == "postgresql"


def _uuid_column_type():
    if _is_postgresql():
        return postgresql.UUID(as_uuid=True)
    # Migration 003 stores MySQL agent ids as CHAR(32) hex (no hyphens).
    return sa.CHAR(32)


def _find_agent_fk_name(bind) -> str | None:
    if _is_postgresql():
        result = bind.execute(
            sa.text(
                """
                SELECT tc.constraint_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                 AND tc.table_schema = kcu.table_schema
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_schema = current_schema()
                  AND tc.table_name = 'chat_sessions'
                  AND kcu.column_name = 'agent_id'
                LIMIT 1
                """
            )
        )
    else:
        result = bind.execute(
            sa.text(
                """
                SELECT CONSTRAINT_NAME
                FROM information_schema.KEY_COLUMN_USAGE
                WHERE TABLE_SCHEMA = DATABASE()
                  AND TABLE_NAME = 'chat_sessions'
                  AND COLUMN_NAME = 'agent_id'
                  AND REFERENCED_TABLE_NAME = 'agents'
                LIMIT 1
                """
            )
        )
    row = result.fetchone()
    return row[0] if row else None


def _string_agent_id_type():
    return sa.String(AGENT_ID_LENGTH)


def _raw_uuid_to_str(raw: object) -> str:
    if isinstance(raw, bytes):
        if len(raw) == 16:
            return str(uuid.UUID(bytes=raw))
        return raw.decode("utf-8")
    return str(raw)


def _normalize_uuid_string(raw: object) -> str:
    text = _raw_uuid_to_str(raw).strip()
    compact = text.replace("-", "")
    if len(compact) == 32:
        return str(uuid.UUID(hex=compact))
    return str(uuid.UUID(text))


def _build_agent_id_map(bind) -> dict[str, str]:
    id_map: dict[str, str] = {}
    rows = bind.execute(sa.text("SELECT id FROM agents")).fetchall()
    for (raw_id,) in rows:
        raw_text = _raw_uuid_to_str(raw_id)
        normalized = _normalize_uuid_string(raw_id)
        id_map[raw_text] = normalized
        compact = raw_text.replace("-", "")
        if compact != raw_text:
            id_map[compact] = normalized
    return id_map


def _resolve_normalized_agent_id(raw: object, id_map: dict[str, str]) -> str | None:
    if raw is None:
        return None
    raw_text = _raw_uuid_to_str(raw)
    if raw_text in id_map:
        return id_map[raw_text]
    compact = raw_text.replace("-", "")
    if compact in id_map:
        return id_map[compact]
    return _normalize_uuid_string(raw)


def _normalize_agent_ids(bind) -> dict[str, str]:
    rows = bind.execute(sa.text("SELECT id FROM agents")).fetchall()
    for (raw_id,) in rows:
        old_text = _raw_uuid_to_str(raw_id)
        new_id = _normalize_uuid_string(raw_id)
        if old_text != new_id:
            bind.execute(
                sa.text("UPDATE agents SET id = :new_id WHERE id = :old_id"),
                {"new_id": new_id, "old_id": raw_id},
            )
    return _build_agent_id_map(bind)


def _normalize_chat_session_references(bind, id_map: dict[str, str]) -> None:
    rows = bind.execute(
        sa.text("SELECT session_key, agent_id FROM chat_sessions WHERE agent_id IS NOT NULL")
    ).fetchall()
    for session_key, agent_id in rows:
        normalized_agent_id = _resolve_normalized_agent_id(agent_id, id_map)
        if normalized_agent_id is None:
            continue

        new_session_key = session_key
        if ":" in session_key:
            _, session_suffix = session_key.split(":", 1)
            new_session_key = f"{normalized_agent_id}:{session_suffix}"

        if new_session_key != session_key:
            bind.execute(
                sa.text(
                    """
                    UPDATE chat_messages
                    SET session_key = :new_session_key
                    WHERE session_key = :old_session_key
                    """
                ),
                {"new_session_key": new_session_key, "old_session_key": session_key},
            )
            bind.execute(
                sa.text(
                    """
                    UPDATE chat_sessions
                    SET session_key = :new_session_key,
                        agent_id = :agent_id
                    WHERE session_key = :old_session_key
                    """
                ),
                {
                    "new_session_key": new_session_key,
                    "old_session_key": session_key,
                    "agent_id": normalized_agent_id,
                },
            )
        elif normalized_agent_id != _raw_uuid_to_str(agent_id):
            bind.execute(
                sa.text(
                    """
                    UPDATE chat_sessions
                    SET agent_id = :agent_id
                    WHERE session_key = :session_key
                    """
                ),
                {"agent_id": normalized_agent_id, "session_key": session_key},
            )


def _drop_agent_fk() -> None:
    bind = op.get_bind()
    fk_name = _find_agent_fk_name(bind)
    if fk_name is None:
        return
    op.drop_constraint(fk_name, "chat_sessions", type_="foreignkey")


def _create_agent_fk() -> None:
    bind = op.get_bind()
    if _find_agent_fk_name(bind) is not None:
        return
    op.create_foreign_key(
        "chat_sessions_agent_id_fkey",
        "chat_sessions",
        "agents",
        ["agent_id"],
        ["id"],
        ondelete="SET NULL",
    )


def _alter_uuid_columns_to_string() -> None:
    uuid_type = _uuid_column_type()
    string_type = _string_agent_id_type()

    if _is_postgresql():
        op.alter_column(
            "agents",
            "id",
            existing_type=uuid_type,
            type_=string_type,
            existing_nullable=False,
            postgresql_using="id::text",
        )
        op.alter_column(
            "chat_sessions",
            "agent_id",
            existing_type=uuid_type,
            type_=string_type,
            existing_nullable=True,
            postgresql_using="agent_id::text",
        )
        return

    op.alter_column(
        "agents",
        "id",
        existing_type=uuid_type,
        type_=string_type,
        existing_nullable=False,
    )
    op.alter_column(
        "chat_sessions",
        "agent_id",
        existing_type=uuid_type,
        type_=string_type,
        existing_nullable=True,
    )


def _alter_string_columns_to_uuid() -> None:
    uuid_type = _uuid_column_type()
    string_type = _string_agent_id_type()

    if _is_postgresql():
        op.alter_column(
            "agents",
            "id",
            existing_type=string_type,
            type_=uuid_type,
            existing_nullable=False,
            postgresql_using="id::uuid",
        )
        op.alter_column(
            "chat_sessions",
            "agent_id",
            existing_type=string_type,
            type_=uuid_type,
            existing_nullable=True,
            postgresql_using="agent_id::uuid",
        )
        return

    op.alter_column(
        "agents",
        "id",
        existing_type=string_type,
        type_=uuid_type,
        existing_nullable=False,
    )
    op.alter_column(
        "chat_sessions",
        "agent_id",
        existing_type=string_type,
        type_=uuid_type,
        existing_nullable=True,
    )


def upgrade() -> None:
    bind = op.get_bind()
    _drop_agent_fk()

    if _is_postgresql():
        _alter_uuid_columns_to_string()
    else:
        # Widen CHAR(32) -> VARCHAR(36) before writing hyphenated UUID strings.
        _alter_uuid_columns_to_string()
        id_map = _normalize_agent_ids(bind)
        _normalize_chat_session_references(bind, id_map)

    _create_agent_fk()


def downgrade() -> None:
    _drop_agent_fk()
    _alter_string_columns_to_uuid()
    _create_agent_fk()
