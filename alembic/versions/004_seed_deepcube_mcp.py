"""seed deepcube builtin MCP server

Revision ID: 004
Revises: 003
Create Date: 2026-06-10

"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, Sequence[str], None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROOT = Path(__file__).resolve().parents[2]
SERVER_FILE = ROOT / "mcps" / "deepcube" / "server.py"


def upgrade() -> None:
    if not SERVER_FILE.is_file():
        return

    mcp_servers = sa.table(
        "mcp_servers",
        sa.column("name", sa.Text()),
        sa.column("transport", sa.Text()),
        sa.column("config", postgresql.JSONB(astext_type=sa.Text())),
        sa.column("description", sa.Text()),
        sa.column("enabled", sa.Boolean()),
    )

    config = {
        "command": sys.executable,
        "args": [str(SERVER_FILE.resolve())],
    }

    op.execute(
        postgresql.insert(mcp_servers)
        .values(
            name="deepcube",
            transport="stdio",
            config=config,
            description="DeepCube SDK assistant: knowledge tools + live cube/dimension/member metadata.",
            enabled=True,
        )
        .on_conflict_do_update(
            index_elements=["name"],
            set_={
                "transport": "stdio",
                "config": config,
                "description": "DeepCube SDK assistant: search, read, and examples (read-only).",
                "enabled": True,
                "updated_at": sa.text("now()"),
            },
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM mcp_servers WHERE name = 'deepcube'"))
