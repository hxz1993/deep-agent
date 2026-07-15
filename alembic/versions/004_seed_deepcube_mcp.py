"""seed deepcube builtin MCP server

Revision ID: 004
Revises: 003
Create Date: 2026-06-10

"""

from __future__ import annotations

from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from deep_agent.db.migration_dialect import is_postgresql, is_sqlite
from sqlalchemy.dialects import postgresql

revision: str = "004"
down_revision: Union[str, Sequence[str], None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROOT = Path(__file__).resolve().parents[2]
SERVER_FILE = ROOT / "mcps" / "deepcube" / "server.py"


def _is_postgresql() -> bool:
    return is_postgresql(op.get_bind())


def _json_type():
    if _is_postgresql():
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def _upsert_mcp_server(
    *,
    name: str,
    transport: str,
    config: dict,
    description: str,
    enabled: bool = True,
) -> None:
    bind = op.get_bind()
    mcp_servers = sa.table(
        "mcp_servers",
        sa.column("name", sa.Text()),
        sa.column("transport", sa.Text()),
        sa.column("config", _json_type()),
        sa.column("description", sa.Text()),
        sa.column("enabled", sa.Boolean()),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    values = dict(
        name=name,
        transport=transport,
        config=config,
        description=description,
        enabled=enabled,
    )
    if is_postgresql(bind):
        op.execute(
            postgresql.insert(mcp_servers)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["name"],
                set_={
                    "transport": transport,
                    "config": config,
                    "description": description,
                    "enabled": enabled,
                    "updated_at": sa.text("now()"),
                },
            )
        )
        return

    if is_sqlite(bind):
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        op.execute(
            sqlite_insert(mcp_servers)
            .values(**values)
            .on_conflict_do_update(
                index_elements=["name"],
                set_={
                    "transport": transport,
                    "config": config,
                    "description": description,
                    "enabled": enabled,
                    "updated_at": sa.text("CURRENT_TIMESTAMP"),
                },
            )
        )
        return

    from sqlalchemy.dialects.mysql import insert as mysql_insert

    op.execute(
        mysql_insert(mcp_servers)
        .values(**values)
        .on_duplicate_key_update(
            transport=transport,
            config=config,
            description=description,
            enabled=enabled,
            updated_at=sa.text("CURRENT_TIMESTAMP"),
        )
    )


def upgrade() -> None:
    if not SERVER_FILE.is_file():
        return

    config = {
        "env": {
            "DEEPCUBE_MCP_TRANSPORT": "stdio",
            "DEEPCUBE_MCP_ENV_URL": "https://test.deepfos.com/deepfos-server/",
            "DEEPCUBE_MCP_HEADERS": "{}",
            "DEEPCUBE_MCP_DIM_INIT_MAX_CONCURRENCY": "4",
        },
    }

    _upsert_mcp_server(
        name="deepcube",
        transport="builtin",
        config=config,
        description="DeepCube SDK assistant: knowledge tools + live cube/dimension/member metadata.",
        enabled=True,
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM mcp_servers WHERE name = 'deepcube'"))
