"""add agents.max_requests

Revision ID: 006
Revises: 005
Create Date: 2026-06-17

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "006"
down_revision: Union[str, Sequence[str], None] = "005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("agents", sa.Column("max_requests", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("agents", "max_requests")
