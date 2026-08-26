"""Replace legacy chat messages and checkpoints with a session event store.

Revision ID: 008
Revises: 007
Create Date: 2026-08-26

This migration intentionally clears legacy conversation history.  Revision-007
history must be exported with ``maintenance.session_history_backup`` before the
upgrade is applied to a shared environment.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import key_type, now_default, text_json_type


revision: str = "008"
down_revision: Union[str, Sequence[str], None] = "007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AGENT_ID_LENGTH = 36


def _bind():
    return op.get_bind()


def _key_type():
    return key_type(_bind())


def _json_type():
    return text_json_type(_bind())


def _now_default():
    return now_default(_bind())


def upgrade() -> None:
    op.drop_table("chat_context_checkpoints")
    op.drop_table("chat_messages")
    op.drop_index("ix_chat_sessions_user_id_agent_id_updated_at", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_agent_id_updated_at", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_updated_at", table_name="chat_sessions")
    op.drop_index("ix_chat_sessions_session_id", table_name="chat_sessions")
    op.drop_table("chat_sessions")

    op.create_table(
        "chat_sessions",
        sa.Column("session_id", _key_type(), nullable=False),
        sa.Column("agent_id", sa.String(AGENT_ID_LENGTH), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("app_id", _key_type(), nullable=True),
        sa.Column("space_id", _key_type(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "next_sequence",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("session_id", name="chat_sessions_pkey"),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name="fk_chat_sessions_agent_id_agents",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_chat_sessions_agent_user_updated",
        "chat_sessions",
        ["agent_id", "user_id", "updated_at"],
    )

    op.create_table(
        "chat_session_events",
        sa.Column("event_id", sa.String(32), nullable=False),
        sa.Column("session_id", _key_type(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("turn_id", sa.String(128), nullable=True),
        sa.Column("run_id", sa.String(128), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=True),
        sa.Column("payload", _json_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("event_id"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.session_id"],
            name="fk_chat_session_events_session_id_sessions",
            ondelete="CASCADE",
        ),
        sa.UniqueConstraint(
            "session_id",
            "sequence",
            name="uq_chat_session_events_session_sequence",
        ),
    )
    op.create_index(
        "ix_chat_session_events_session_turn_sequence",
        "chat_session_events",
        ["session_id", "turn_id", "sequence"],
    )
    op.create_index(
        "ix_chat_session_events_session_run_sequence",
        "chat_session_events",
        ["session_id", "run_id", "sequence"],
    )
    op.create_index(
        "ix_chat_session_events_session_type_status_sequence",
        "chat_session_events",
        ["session_id", "event_type", "status", "sequence"],
    )


def downgrade() -> None:
    op.drop_table("chat_session_events")
    op.drop_index("ix_chat_sessions_agent_user_updated", table_name="chat_sessions")
    op.drop_table("chat_sessions")

    op.create_table(
        "chat_sessions",
        sa.Column(
            "agent_id",
            sa.String(AGENT_ID_LENGTH),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("session_id", _key_type(), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("agent_id", "session_id", name="chat_sessions_pkey"),
    )
    op.create_index("ix_chat_sessions_session_id", "chat_sessions", ["session_id"])
    op.create_index("ix_chat_sessions_updated_at", "chat_sessions", ["updated_at"])
    op.create_index(
        "ix_chat_sessions_agent_id_updated_at",
        "chat_sessions",
        ["agent_id", "updated_at"],
    )
    op.create_index(
        "ix_chat_sessions_user_id_agent_id_updated_at",
        "chat_sessions",
        ["user_id", "agent_id", "updated_at"],
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "agent_id",
            sa.String(AGENT_ID_LENGTH),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("session_id", _key_type(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload", _json_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_id",
            "session_id",
            "sequence",
            name="uq_chat_messages_session_sequence",
        ),
    )
    op.create_table(
        "chat_context_checkpoints",
        sa.Column("agent_id", sa.String(AGENT_ID_LENGTH), nullable=False),
        sa.Column("session_id", _key_type(), nullable=False),
        sa.Column("source_last_sequence", sa.Integer(), nullable=False),
        sa.Column("source_history_hash", sa.String(64), nullable=False),
        sa.Column("payload", _json_type(), nullable=False),
        sa.Column("payload_schema_version", sa.Integer(), nullable=False),
        sa.Column("projection_fingerprint", sa.String(64), nullable=False),
        sa.Column("estimated_tokens", sa.BigInteger(), nullable=False),
        sa.Column("summary_model_id", sa.String(AGENT_ID_LENGTH), nullable=True),
        sa.Column(
            "row_version", sa.BigInteger(), server_default=sa.text("1"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("agent_id", "session_id"),
    )
