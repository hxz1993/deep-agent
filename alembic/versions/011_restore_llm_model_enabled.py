"""Restore model-level availability without restoring Provider switches.

Revision ID: 011
Revises: 010
Create Date: 2026-08-28

Providers remain available whenever their real configuration is valid.  A
model-level switch is restored so administrators can keep a configured model
without offering it to Agents or request-level model selection.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op


revision: str = "011"
down_revision: Union[str, Sequence[str], None] = "010"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("llm_models") as batch:
        batch.add_column(
            sa.Column(
                "enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("llm_models") as batch:
        batch.drop_column("enabled")
