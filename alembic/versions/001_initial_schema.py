"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-06-05

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _json_type():
    if _is_postgresql():
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def _key_type():
    if _is_postgresql():
        return sa.Text()
    return sa.String(191)


def _now_default():
    if _is_postgresql():
        return sa.text("now()")
    return sa.text("CURRENT_TIMESTAMP")


def _insert_default_agent() -> None:
    if _is_postgresql():
        op.execute(
            sa.text(
                """
                INSERT INTO agents (name, model, prompt)
                VALUES (
                    'default',
                    'openai:gpt-4o',
                    'You are deep-agent, a helpful general-purpose assistant. '
                    'Use the available tools when they help you answer accurately. '
                    'Be concise and explain your reasoning when it is useful.'
                )
                ON CONFLICT (name) DO NOTHING
                """
            )
        )
        return
    op.execute(
        sa.text(
            """
            INSERT IGNORE INTO agents (name, model, prompt)
            VALUES (
                'default',
                'openai:gpt-4o',
                'You are deep-agent, a helpful general-purpose assistant. '
                'Use the available tools when they help you answer accurately. '
                'Be concise and explain your reasoning when it is useful.'
            )
            """
        )
    )


def upgrade() -> None:
    op.create_table(
        "agents",
        sa.Column("name", _key_type(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("api_key", sa.Text(), nullable=True),
        sa.Column("capabilities", _json_type(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )

    op.create_table(
        "chat_sessions",
        sa.Column("session_key", _key_type(), nullable=False),
        sa.Column("agent_name", _key_type(), nullable=True),
        sa.Column("user_id", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.ForeignKeyConstraint(
            ["agent_name"],
            ["agents.name"],
            ondelete="SET NULL",
            name="chat_sessions_agent_name_fkey",
        ),
        sa.PrimaryKeyConstraint("session_key"),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("session_key", _key_type(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload", _json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_key"],
            ["chat_sessions.session_key"],
            ondelete="CASCADE",
            name="chat_messages_session_key_fkey",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_key", "sequence", name="uq_chat_messages_session_sequence"),
    )

    _insert_default_agent()


def downgrade() -> None:
    op.drop_table("chat_messages")
    op.drop_table("chat_sessions")
    op.drop_table("agents")
