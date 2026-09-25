"""Persist per-request usage on its existing session event.

Revision ID: 007
Revises: 006
"""
from alembic import op
import sqlalchemy as sa
from deep_agent.db.types import JSONType
from deep_agent.db.codex_schema import ensure_codex_columns

revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade():
    usage_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("chat_session_events")}
    if "usage_model_id" not in usage_columns:
        op.add_column("chat_session_events", sa.Column("usage_model_id", sa.String(36), nullable=True))
    if "usage" not in usage_columns:
        op.add_column("chat_session_events", sa.Column("usage", JSONType(), nullable=True))
    indexes = {index["name"] for index in sa.inspect(op.get_bind()).get_indexes("chat_session_events")}
    if "ix_chat_session_events_usage_created" not in indexes:
        op.create_index("ix_chat_session_events_usage_created", "chat_session_events", ["created_at", "usage_model_id"])
    ensure_codex_columns(op)


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("007 downgrade requires online usage validation")
    if op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM chat_session_events WHERE usage IS NOT NULL")):
        raise RuntimeError("Cannot downgrade: session usage exists; export or remove it explicitly first.")
    provider_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("llm_providers")}
    model_columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("llm_models")}
    if "auth_method" in provider_columns and op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM llm_providers WHERE auth_method != 'api_key'")):
        raise RuntimeError("Cannot downgrade: subscription providers exist.")
    if "purpose" in model_columns and op.get_bind().scalar(sa.text("SELECT COUNT(*) FROM llm_models WHERE purpose != 'chat'")):
        raise RuntimeError("Cannot downgrade: non-chat models exist.")
    for column in ("auth_status", "auth_revision", "credential_json", "auth_method"):
        if column in provider_columns:
            op.drop_column("llm_providers", column)
    if "purpose" in model_columns:
        op.drop_column("llm_models", "purpose")
    op.drop_index("ix_chat_session_events_usage_created", table_name="chat_session_events")
    op.drop_column("chat_session_events", "usage")
    op.drop_column("chat_session_events", "usage_model_id")
