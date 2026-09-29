"""Add webhook credentials; permit non-bot channel accounts.

Revision ID: 011
Revises: 010
"""
from alembic import op
import sqlalchemy as sa
from deep_agent.db.session_source_schema import ensure_session_source_columns

revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade():
    # Complete the approved 010 source columns for databases stamped by an
    # earlier development build; existing 010 columns and data stay unchanged.
    ensure_session_source_columns(op)
    with op.batch_alter_table("channel_accounts") as batch:
        batch.add_column(sa.Column("webhook_token", sa.Text(), nullable=True))
        batch.add_column(sa.Column("webhook_aes_key", sa.Text(), nullable=True))
        batch.alter_column("bot_id", existing_type=sa.String(191), existing_nullable=False, nullable=True)
        batch.alter_column("secret", existing_type=sa.Text(), existing_nullable=False, nullable=True)


def downgrade():
    raise RuntimeError("011 cannot be downgraded automatically: webhook credentials and non-bot accounts may exist. Restore a pre-migration backup with matching code.")
