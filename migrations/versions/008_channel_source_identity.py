"""Persist channel sender and conversation source facts without new tables.

Revision ID: 008
Revises: 007
"""
from alembic import op
import sqlalchemy as sa
from deep_agent.db.types import JSONType
from deep_agent.db.codex_schema import ensure_codex_columns

revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade():
    # Early local 007 snapshots predate subscription columns. Repair before
    # advancing their revision, preserving the existing startup guarantee.
    ensure_codex_columns(op)
    # Shared channel semantics, not Feishu-only columns. Platform-specific
    # attributes live under wecom_* / lark_* keys in source_metadata.
    for name in ("sender_name", "tenant_id", "thread_id"):
        op.add_column("channel_message_runs", sa.Column(name, sa.String(191), nullable=True))
    op.add_column("channel_message_runs", sa.Column("source_metadata", JSONType(), nullable=True))


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("008 downgrade requires online data validation")
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT count(*) FROM channel_accounts WHERE channel_type = 'feishu'")):
        raise RuntimeError("Cannot downgrade: Feishu channels exist; remove them explicitly first.")
    if bind.scalar(sa.text("SELECT count(*) FROM channel_message_runs WHERE sender_name IS NOT NULL OR tenant_id IS NOT NULL OR thread_id IS NOT NULL OR source_metadata IS NOT NULL")):
        raise RuntimeError("Cannot downgrade: channel source facts exist; archive them explicitly first.")
    with op.batch_alter_table("channel_message_runs") as batch:
        for name in ("source_metadata", "thread_id", "tenant_id", "sender_name"):
            batch.drop_column(name)
