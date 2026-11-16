"""Persist Agent publication and remote connection configuration.

Revision ID: 016
Revises: 015
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.mysql import LONGTEXT

revision = "016"
down_revision = "015"
branch_labels = None
depends_on = None

_TABLE = "agent_publications"
_INDEX = "ix_agent_publications_agent_id"
_CONNECTIONS = "remote_agent_connections"


def upgrade():
    op.create_table(
        _TABLE,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.String(240), nullable=False),
        sa.Column("agent_id", sa.String(36), nullable=False),
        sa.Column("scope", sa.Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("code", name="uq_agent_publications_code"),
    )
    op.create_index(_INDEX, _TABLE, ["agent_id"])
    json_text = sa.Text().with_variant(LONGTEXT(), "mysql")
    op.create_table(
        _CONNECTIONS,
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("config", json_text, nullable=False),
        sa.Column("inspection", json_text, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("name", name="uq_remote_agent_connections_name"),
    )


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("Downgrade 016 online to 015 first.")
    table = sa.table(_TABLE, sa.column("id"))
    if op.get_bind().scalar(sa.select(table.c.id).limit(1)) is not None:
        raise RuntimeError("016 downgrade would discard Agent publications.")
    connections = sa.table(_CONNECTIONS, sa.column("id"))
    if op.get_bind().scalar(sa.select(connections.c.id).limit(1)) is not None:
        raise RuntimeError("016 downgrade would discard remote Agent connections.")
    op.drop_table(_CONNECTIONS)
    op.drop_index(_INDEX, table_name=_TABLE)
    op.drop_table(_TABLE)
