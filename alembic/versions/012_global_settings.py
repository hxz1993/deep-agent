"""Add generic versioned global settings and seed Web Search.

Revision ID: 012
Revises: 011
Create Date: 2026-08-30
"""

from __future__ import annotations

from collections.abc import Sequence
import json
from typing import Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import key_type, now_default, text_json_type


revision: str = "012"
down_revision: Union[str, Sequence[str], None] = "011"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    json_type = text_json_type(bind)
    op.create_table(
        "global_settings",
        sa.Column("key", key_type(bind), nullable=False),
        sa.Column("value", json_type, nullable=False),
        sa.Column("version", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=now_default(bind),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=now_default(bind),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("key"),
    )
    settings = sa.table(
        "global_settings",
        sa.column("key", key_type(bind)),
        sa.column("value", json_type),
        sa.column("version", sa.SmallInteger()),
    )
    bind.execute(
        settings.insert().values(
            key="web_search",
            value=json.dumps(
                {"mode": "native"},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            version=1,
        )
    )


def downgrade() -> None:
    op.drop_table("global_settings")
