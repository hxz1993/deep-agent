"""Agent output definitions and trusted configuration updater.

Revision ID: 012
Revises: 011
"""
from alembic import op
import sqlalchemy as sa

from deep_agent.db.types import JSONType, UtcTimestamp

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("agents") as batch:
        batch.add_column(sa.Column("structured_output_enabled", sa.Boolean(), nullable=False, server_default=sa.false()))
        batch.add_column(sa.Column("updated_by", sa.String(128), nullable=True))
    op.create_table(
        "agent_output_configs",
        sa.Column("agent_id", sa.String(36), primary_key=True, nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("json_schema", JSONType(), nullable=False),
        sa.Column("created_at", UtcTimestamp(), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", UtcTimestamp(), nullable=False, server_default=sa.func.now()),
    )


def downgrade():
    connection = op.get_bind()
    checks = {
        "output_configs": "SELECT COUNT(*) FROM agent_output_configs",
        "enabled_agents": "SELECT COUNT(*) FROM agents WHERE structured_output_enabled = :enabled",
        "updated_agents": "SELECT COUNT(*) FROM agents WHERE updated_by IS NOT NULL",
        "output_events": "SELECT COUNT(*) FROM chat_session_events WHERE event_type = 'output/attempt'",
        "output_snapshots": "SELECT COUNT(*) FROM chat_session_events WHERE payload LIKE '%deepagent_output_contract%'",
    }
    counts = {key: int(connection.scalar(sa.text(sql), {"enabled": True}) or 0) for key, sql in checks.items()}
    # Disabled output definitions, updater facts and accepted/failed attempts are
    # valuable data too. Never silently discard them as a side effect of rollback.
    if any(counts.values()):
        raise RuntimeError("012 downgrade would discard structured-output or updater facts: "
                           + ", ".join(f"{key}={value}" for key, value in counts.items())
                           + ". Keep 012 and disable output, or restore an approved backup.")
    op.drop_table("agent_output_configs")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("updated_by")
        batch.drop_column("structured_output_enabled")
