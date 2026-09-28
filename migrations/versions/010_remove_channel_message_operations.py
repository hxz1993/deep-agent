"""Remove the standalone outbound receipt table.

Revision ID: 010
Revises: 009

Only obsolete outbound receipts are deleted. Channel runs, sessions, files and
configuration retain their existing schema and data.
"""
from alembic import op

revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_table("channel_message_operations")


def downgrade():
    raise RuntimeError(
        "010 cannot be downgraded: deleted outbound receipts cannot be recovered. "
        "Restore an explicitly selected pre-010 database backup together with its matching application version."
    )
