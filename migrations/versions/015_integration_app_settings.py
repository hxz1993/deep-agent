"""Persist integration scope settings and application Skill ownership.

Revision ID: 015
Revises: 014
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT

revision = "015"
down_revision = "014"
branch_labels = None
depends_on = None

_TABLE = "integration_scoped_settings"
_SCOPE_INDEX = "ix_integration_scoped_settings_tenant"
_SKILL_INDEX = "ix_skill_packages_scope_ref"


def upgrade():
    context = op.get_context()
    inspector = None if context.as_sql else sa.inspect(op.get_bind())
    if inspector is None or not inspector.has_table(_TABLE):
        op.create_table(
            _TABLE,
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("integration_code", sa.String(64), nullable=False),
            sa.Column("scope_type", sa.String(16), nullable=False),
            sa.Column("scope_key", sa.String(191), nullable=False),
            sa.Column("tenant_id", sa.String(191), nullable=False),
            sa.Column("settings", sa.Text().with_variant(LONGTEXT(), "mysql"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.UniqueConstraint("integration_code", "scope_type", "scope_key", name="uq_integration_scoped_settings_scope"),
        )
        op.create_index(_SCOPE_INDEX, _TABLE, ["integration_code", "scope_type", "tenant_id"])
    else:
        columns = {item["name"] for item in inspector.get_columns(_TABLE)}
        if columns != {"id", "integration_code", "scope_type", "scope_key", "tenant_id", "settings", "created_at", "updated_at"}:
            raise RuntimeError("Cannot resume 015: unexpected integration scope table shape")
        if not any(item["column_names"] == ["integration_code", "scope_type", "scope_key"]
                   for item in inspector.get_unique_constraints(_TABLE)):
            raise RuntimeError("Cannot resume 015: missing integration scope uniqueness")
        if not any(item["name"] == _SCOPE_INDEX for item in inspector.get_indexes(_TABLE)):
            op.create_index(_SCOPE_INDEX, _TABLE, ["integration_code", "scope_type", "tenant_id"])
    scope = None if inspector is None else next(
        (column for column in inspector.get_columns("skill_packages") if column["name"] == "scope_ref"), None
    )
    if scope is None:
        op.add_column("skill_packages", sa.Column("scope_ref", sa.String(64), nullable=True))
    elif not (isinstance(scope["type"], sa.String) and scope["type"].length == 64
              and scope["nullable"] and scope["default"] is None):
        raise RuntimeError("Cannot resume 015: unexpected skill_packages.scope_ref definition")
    if inspector is None or not any(item["name"] == _SKILL_INDEX for item in inspector.get_indexes("skill_packages")):
        op.create_index(_SKILL_INDEX, "skill_packages", ["scope_ref"])


def downgrade():
    if op.get_context().as_sql or op.get_context().opts.get("destination_rev") != "014":
        raise RuntimeError("Downgrade 015 online to 014 first, then evaluate any further downgrade.")
    connection = op.get_bind()
    scopes = sa.table(_TABLE, sa.column("id"))
    skills = sa.table("skill_packages", sa.column("scope_ref"))
    if (connection.scalar(sa.select(scopes.c.id).limit(1)) or
            connection.scalar(sa.select(skills.c.scope_ref).where(skills.c.scope_ref != "general").limit(1)) is not None):
        raise RuntimeError("015 downgrade would discard application configuration or Skills.")
    op.drop_index(_SKILL_INDEX, table_name="skill_packages")
    with op.batch_alter_table("skill_packages") as batch:
        batch.drop_column("scope_ref")
    op.drop_table(_TABLE)
