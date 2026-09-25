"""Idempotent portion of the unpublished 007 Provider/Model migration."""

from alembic.operations import Operations
import sqlalchemy as sa

from deep_agent.db.types import JSONType


def ensure_codex_columns(operations: Operations) -> None:
    """Add only missing columns, including databases already marked old 007."""
    bind = operations.get_bind()
    for table, columns in (
        ("llm_providers", (
            sa.Column("auth_method", sa.String(32), nullable=False, server_default="api_key"),
            sa.Column("credential_json", JSONType(), nullable=True),
            sa.Column("auth_revision", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("auth_status", sa.String(32), nullable=False, server_default="unconfigured"),
        )),
        ("llm_models", (
            sa.Column("purpose", sa.String(32), nullable=False, server_default="chat"),
        )),
    ):
        existing = {column["name"] for column in sa.inspect(bind).get_columns(table)}
        for column in columns:
            if column.name not in existing:
                operations.add_column(table, column)
