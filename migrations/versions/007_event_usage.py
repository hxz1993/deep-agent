"""Persist per-request usage on its existing session event.

Revision ID: 007
Revises: 006
"""
from alembic import op
import sqlalchemy as sa
from deep_agent.db.types import JSONType

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("chat_session_events", sa.Column("usage_model_id", sa.String(36), nullable=True))
    op.add_column("chat_session_events", sa.Column("usage", JSONType(), nullable=True))
    op.create_index("ix_chat_session_events_usage_created", "chat_session_events", ["created_at", "usage_model_id"])


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("007 downgrade requires online usage validation")
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM chat_session_events WHERE usage IS NOT NULL")):
        raise RuntimeError("Cannot downgrade: session usage exists; export or remove it explicitly first.")
    op.drop_index("ix_chat_session_events_usage_created", table_name="chat_session_events")
    op.drop_column("chat_session_events", "usage")
    op.drop_column("chat_session_events", "usage_model_id")
