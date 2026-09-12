"""Allow unconfigured Agent models and remove sampling temperature; scope UI tools.

Revision ID: 005
Revises: 004
"""
from alembic import op
import sqlalchemy as sa
import json

from deep_agent.db.types import JSONType

revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The unpublished local V5 originally contained temperature. Inspecting the
    # online shape also permits an explicit, idempotent local V5 repair without
    # rewriting alembic_version. Offline SQL describes normal 004 -> 005 only.
    columns = None if op.get_context().as_sql else {
        item['name']: item for item in sa.inspect(op.get_bind()).get_columns('agents')
    }
    with op.batch_alter_table("agents") as batch:
        if columns is None or not columns['model_id']['nullable']:
            batch.alter_column("model_id", existing_type=sa.String(36), nullable=True)
        if columns is None or 'temperature' in columns:
            batch.drop_column('temperature', existing_type=sa.Float())

    if columns is None or 'ui_tool_selection' not in columns:
        # Text JSON has no server default on older MySQL. Backfill explicitly
        # before enforcing NOT NULL, leaving ORM inserts to supply {}.
        with op.batch_alter_table("agents") as batch:
            batch.add_column(sa.Column("ui_tool_selection", JSONType(), nullable=True))
    if (columns is None or 'ui_tool_selection' not in columns
            or columns['ui_tool_selection']['nullable']):
        # A previous nontransactional DDL attempt may have added the nullable
        # column already. Complete both backfill and constraint on retry.
        op.execute(sa.text("UPDATE agents SET ui_tool_selection = '{}' WHERE ui_tool_selection IS NULL"))
        with op.batch_alter_table("agents") as batch:
            batch.alter_column("ui_tool_selection", existing_type=JSONType(), nullable=False)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.scalar(sa.text("SELECT count(*) FROM agents WHERE model_id IS NULL")):
        raise RuntimeError("Cannot downgrade: Agents without a model exist; configure their models first.")
    for value in bind.execute(sa.text("SELECT ui_tool_selection FROM agents")).scalars():
        selection = json.loads(value) if isinstance(value, str) else value
        if selection:
            raise RuntimeError("Cannot downgrade: Agent UI tool selections exist; clear their scopes first.")
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("ui_tool_selection")
        batch.alter_column("model_id", existing_type=sa.String(36), nullable=False)
        # Removed per-Agent choices are not reconstructed. NULL selects the
        # model/provider default, matching the old column's original default.
        batch.add_column(sa.Column('temperature', sa.Float(), nullable=True))
