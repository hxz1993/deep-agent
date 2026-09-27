"""Durable outbound channel operation receipts.

Revision ID: 009
Revises: 008
"""
from alembic import op
import sqlalchemy as sa

from deep_agent.db.types import JSONType, UtcTimestamp

revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "channel_message_operations",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("account_id", sa.String(32), nullable=False),
        sa.Column("channel_run_id", sa.String(32), nullable=False),
        sa.Column("agent_run_id", sa.String(128).with_variant(sa.String(128, collation="utf8mb4_bin"), "mysql"), nullable=False),
        sa.Column("tool_call_id", sa.String(191).with_variant(sa.String(191, collation="utf8mb4_bin"), "mysql"), nullable=False),
        sa.Column("session_id", sa.String(191), nullable=False),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("state", sa.String(32), nullable=False, server_default="pending"),
        sa.Column("results", JSONType(), nullable=False),
        *[sa.Column(name, UtcTimestamp(), nullable=False, server_default=sa.func.now())
          for name in ("created_at", "updated_at")],
        sa.UniqueConstraint("agent_run_id", "tool_call_id", name="uq_channel_operations_run_tool"),
    )
    for suffix in ("account", "session", "state"):
        column = suffix if suffix == "state" else suffix + "_id"
        op.create_index("ix_channel_operations_" + suffix, "channel_message_operations", [column])


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("009 downgrade requires online data validation")
    if op.get_bind().scalar(sa.text("SELECT count(*) FROM channel_message_operations")):
        raise RuntimeError("Cannot downgrade: outbound receipts exist; archive them explicitly first.")
    op.drop_table("channel_message_operations")
