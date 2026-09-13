"""WeCom applications and rebuildable user profiles.

Revision ID: 006
Revises: 005
"""
import json
from alembic import op
import sqlalchemy as sa
from deep_agent.db.types import JSONType, UtcTimestamp

revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def timestamps():
    return [sa.Column(name, UtcTimestamp(), nullable=False, server_default=sa.func.now()) for name in ("created_at", "updated_at")]


def upgrade():
    op.create_table("wecom_applications",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("name", sa.String(191), nullable=False),
        sa.Column("corp_id", sa.String(191).with_variant(sa.String(191, collation="utf8mb4_bin"), "mysql"), nullable=False),
        sa.Column("wecom_agent_id", sa.String(191).with_variant(sa.String(191, collation="utf8mb4_bin"), "mysql"), nullable=False),
        sa.Column("app_secret", sa.Text(), nullable=False),
        sa.Column("settings", JSONType(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        *timestamps(),
        sa.UniqueConstraint("corp_id", "wecom_agent_id", name="uq_wecom_application_identity"),
    )
    op.create_table("wecom_user_profiles",
        sa.Column("id", sa.String(32), primary_key=True),
        sa.Column("wecom_application_id", sa.String(32), nullable=False),
        sa.Column("open_userid", sa.String(191).with_variant(sa.String(191, collation="utf8mb4_bin"), "mysql"), nullable=True),
        sa.Column("userid", sa.String(191), nullable=False),
        sa.Column("userid_key", sa.String(191).with_variant(sa.String(191, collation="utf8mb4_bin"), "mysql"), nullable=False),
        sa.Column("name", sa.String(191), nullable=True),
        *[sa.Column(name, UtcTimestamp(), nullable=True) for name in ("profile_synced_at", "expires_at", "last_attempt_at", "next_retry_at")],
        sa.Column("last_error_stage", sa.String(64), nullable=True),
        sa.Column("last_error_code", sa.String(64), nullable=True),
        *timestamps(),
        sa.UniqueConstraint("wecom_application_id", "userid_key", name="uq_wecom_profile_userid"),
        sa.UniqueConstraint("wecom_application_id", "open_userid", name="uq_wecom_profile_open_userid"),
    )


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("006 downgrade requires online data validation")
    bind = op.get_bind()
    for table in ("wecom_applications", "wecom_user_profiles"):
        if bind.scalar(sa.text(f"SELECT count(*) FROM {table}")):
            raise RuntimeError("Cannot downgrade: WeCom configuration or member profiles exist; remove them explicitly first.")
    for raw in bind.execute(sa.text("SELECT settings FROM channel_accounts")).scalars():
        value = json.loads(raw) if isinstance(raw, str) else raw
        if (value or {}).get("wecom"):
            raise RuntimeError("Cannot downgrade: channel WeCom settings exist; remove them explicitly first.")
    op.drop_table("wecom_user_profiles")
    op.drop_table("wecom_applications")
