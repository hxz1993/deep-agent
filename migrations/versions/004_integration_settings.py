"""Add sparse component integration configuration, without seed rows.

Revision ID: 004
Revises: 003
"""
from alembic import op
import sqlalchemy as sa
from deep_agent.db.migration_dialect import now_default, text_json_type

revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    op.create_table(
        "integration_settings",
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("settings", text_json_type(bind), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=now_default(bind), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=now_default(bind), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )


def downgrade() -> None:
    op.drop_table("integration_settings")
