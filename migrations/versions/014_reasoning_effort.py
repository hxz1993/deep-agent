"""Store model effort options and an Agent's default effort.

Revision ID: 014
Revises: 013
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.mysql import LONGTEXT

revision = "014"
down_revision = "013"
branch_labels = None
depends_on = None

_COLUMNS = (("llm_models", "reasoning_options"), ("agents", "reasoning_effort"))


def upgrade():
    existing = set()
    context = op.get_context()
    if not context.as_sql:
        # MySQL can commit the first ADD COLUMN even if the migration stops
        # before the second. Check both shapes before resuming an upgrade.
        inspector = sa.inspect(op.get_bind())
        expected_type = LONGTEXT if context.dialect.name == "mysql" else sa.Text
        for table, name in _COLUMNS:
            for column in inspector.get_columns(table):
                if column["name"] == name:
                    if not (isinstance(column["type"], expected_type) and column["nullable"] and column["default"] is None):
                        raise RuntimeError(f"Cannot resume 014: unexpected column definition for {table}.{name}")
                    existing.add((table, name))
    for table, name in _COLUMNS:
        if (table, name) not in existing:
            op.add_column(table, sa.Column(name, sa.Text().with_variant(LONGTEXT(), "mysql"), nullable=True))


def downgrade():
    if op.get_context().as_sql:
        raise RuntimeError("014 requires an online downgrade to verify saved reasoning configuration.")
    connection = op.get_bind()
    for name, field in _COLUMNS:
        table = sa.table(name, sa.column(field))
        if connection.scalar(sa.select(sa.literal(1)).select_from(table).where(table.c[field].is_not(None)).limit(1)):
            raise RuntimeError("014 downgrade would discard saved reasoning configuration. Keep 014 or restore an approved backup.")
    # Older downgrade steps have their own guards. Stop at 013 before invoking them,
    # so a later refusal cannot leave an implicit MySQL DDL commit behind.
    if op.get_context().opts.get("destination_rev") != "013":
        raise RuntimeError("Downgrade 014 to 013 first, then evaluate any further downgrade.")
    for table, name in reversed(_COLUMNS):
        with op.batch_alter_table(table) as batch:
            batch.drop_column(name)
