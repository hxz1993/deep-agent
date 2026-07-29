"""remove per-agent request limit

Revision ID: 021
Revises: 020
Create Date: 2026-07-27

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import has_column, is_sqlite


revision: str = "021"
down_revision: Union[str, Sequence[str], None] = "020"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "agents", "max_requests"):
        return
    if is_sqlite(bind):
        with op.batch_alter_table("agents") as batch_op:
            batch_op.drop_column("max_requests")
    else:
        op.drop_column("agents", "max_requests")


def downgrade() -> None:
    bind = op.get_bind()
    if has_column(bind, "agents", "max_requests"):
        return
    op.add_column(
        "agents",
        sa.Column("max_requests", sa.Integer(), nullable=True),
    )
