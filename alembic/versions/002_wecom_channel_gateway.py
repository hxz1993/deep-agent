"""Add optional enterprise WeCom Channel Gateway storage.

Revision ID: 002
Revises: 001
Create Date: 2026-09-03
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import (
    boolean_default,
    key_type,
    now_default,
    text_json_type,
)


revision: str = "002"
down_revision: Union[str, Sequence[str], None] = "001"
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


def _false_default():
    return boolean_default(_bind(), value=False)


def upgrade() -> None:
    op.create_table(
        "channel_accounts",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("name", _key_type(), nullable=False),
        sa.Column("channel_type", sa.String(32), server_default="wecom", nullable=False),
        sa.Column("transport", sa.String(32), server_default="websocket", nullable=False),
        sa.Column("bot_id", _key_type(), nullable=False),
        sa.Column("secret", sa.Text(), nullable=False),
        sa.Column("agent_id", sa.String(AGENT_ID_LENGTH), nullable=False),
        sa.Column("session_user_id", sa.String(128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=_false_default(), nullable=False),
        sa.Column("settings", _json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.ForeignKeyConstraint(["agent_id"], ["agents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("channel_type", "bot_id", name="uq_channel_accounts_type_bot"),
    )
    op.create_index("ix_channel_accounts_agent_id", "channel_accounts", ["agent_id"])
    op.create_index("ix_channel_accounts_enabled", "channel_accounts", ["enabled"])

    op.create_table(
        "channel_session_bindings",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("account_id", sa.String(32), nullable=False),
        sa.Column("peer_type", sa.String(16), nullable=False),
        sa.Column("peer_id", _key_type(), nullable=False),
        sa.Column("peer_display_name", sa.Text(), nullable=True),
        sa.Column("session_id", _key_type(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.ForeignKeyConstraint(["account_id"], ["channel_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["session_id"], ["chat_sessions.session_id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "peer_type",
            "peer_id",
            name="uq_channel_bindings_account_peer",
        ),
    )
    op.create_index("ix_channel_session_bindings_account_id", "channel_session_bindings", ["account_id"])
    op.create_index("ix_channel_bindings_session_id", "channel_session_bindings", ["session_id"])

    op.create_table(
        "channel_message_runs",
        sa.Column("id", sa.String(32), nullable=False),
        sa.Column("account_id", sa.String(32), nullable=False),
        sa.Column("binding_id", sa.String(32), nullable=False),
        sa.Column("external_message_id", _key_type(), nullable=False),
        sa.Column("external_request_id", _key_type(), nullable=True),
        sa.Column("sender_id", _key_type(), nullable=True),
        sa.Column("session_id", _key_type(), nullable=True),
        sa.Column("agent_run_id", sa.String(128), nullable=True),
        sa.Column("stream_id", _key_type(), nullable=True),
        sa.Column("payload_hash", sa.String(64), nullable=False),
        sa.Column("processing_status", sa.String(32), server_default="received", nullable=False),
        sa.Column("delivery_status", sa.String(32), server_default="pending", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="1", nullable=False),
        sa.Column("error_code", sa.String(64), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["channel_accounts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["binding_id"], ["channel_session_bindings.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "account_id",
            "external_message_id",
            name="uq_channel_runs_account_message",
        ),
    )
    op.create_index("ix_channel_message_runs_account_id", "channel_message_runs", ["account_id"])
    op.create_index("ix_channel_runs_binding_created", "channel_message_runs", ["binding_id", "created_at"])
    op.create_index("ix_channel_runs_processing_status", "channel_message_runs", ["processing_status"])


def downgrade() -> None:
    op.drop_index("ix_channel_runs_processing_status", table_name="channel_message_runs")
    op.drop_index("ix_channel_runs_binding_created", table_name="channel_message_runs")
    op.drop_index("ix_channel_message_runs_account_id", table_name="channel_message_runs")
    op.drop_table("channel_message_runs")
    op.drop_index("ix_channel_bindings_session_id", table_name="channel_session_bindings")
    op.drop_index("ix_channel_session_bindings_account_id", table_name="channel_session_bindings")
    op.drop_table("channel_session_bindings")
    op.drop_index("ix_channel_accounts_enabled", table_name="channel_accounts")
    op.drop_index("ix_channel_accounts_agent_id", table_name="channel_accounts")
    op.drop_table("channel_accounts")
