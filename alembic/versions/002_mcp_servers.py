"""mcp_servers table and seed data

Revision ID: 002
Revises: 001
Create Date: 2026-06-05

"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "002"
down_revision: Union[str, Sequence[str], None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROOT = Path(__file__).resolve().parents[2]
MCP_JSON = ROOT / "config" / "mcp.json"
MCPS_DIR = ROOT / "mcps"
SERVER_FILENAME = "server.py"


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _json_type():
    if _is_postgresql():
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def _boolean_true_default():
    if _is_postgresql():
        return sa.text("true")
    return sa.text("1")


def _now_default():
    if _is_postgresql():
        return sa.text("now()")
    return sa.text("CURRENT_TIMESTAMP")


def _key_type():
    if _is_postgresql():
        return sa.Text()
    return sa.String(191)


def _seed_rows() -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()

    if MCP_JSON.is_file():
        try:
            raw = json.loads(MCP_JSON.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            raw = {}
        for name, server in raw.get("mcpServers", {}).items():
            if name in seen:
                continue
            if "url" in server:
                config: dict = {"url": server["url"]}
                if server.get("headers"):
                    config["headers"] = server["headers"]
                rows.append(
                    {
                        "name": name,
                        "transport": "url",
                        "config": config,
                        "description": f"MCP server '{name}' (imported from config/mcp.json).",
                        "enabled": True,
                    }
                )
                seen.add(name)
            elif "command" in server:
                config = {"command": server["command"], "args": list(server.get("args", []))}
                if server.get("env"):
                    config["env"] = server["env"]
                if server.get("cwd"):
                    config["cwd"] = server["cwd"]
                rows.append(
                    {
                        "name": name,
                        "transport": "stdio",
                        "config": config,
                        "description": f"MCP server '{name}' (imported from config/mcp.json).",
                        "enabled": True,
                    }
                )
                seen.add(name)

    if MCPS_DIR.is_dir():
        for entry in sorted(MCPS_DIR.iterdir()):
            server_file = entry / SERVER_FILENAME
            if not (entry.is_dir() and server_file.is_file()):
                continue
            name = entry.name
            if name in seen:
                continue
            rows.append(
                {
                    "name": name,
                    "transport": "builtin",
                    "config": {},
                    "description": f"Builtin MCP server '{name}'.",
                    "enabled": True,
                }
            )
            seen.add(name)

    return rows


def upgrade() -> None:
    op.create_table(
        "mcp_servers",
        sa.Column("name", _key_type(), nullable=False),
        sa.Column("transport", sa.Text(), nullable=False),
        sa.Column("config", _json_type(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=_boolean_true_default(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )

    mcp_servers = sa.table(
        "mcp_servers",
        sa.column("name", sa.Text()),
        sa.column("transport", sa.Text()),
        sa.column("config", _json_type()),
        sa.column("description", sa.Text()),
        sa.column("enabled", sa.Boolean()),
    )

    for row in _seed_rows():
        op.execute(
            mcp_servers.insert().values(
                name=row["name"],
                transport=row["transport"],
                config=row["config"],
                description=row["description"],
                enabled=row["enabled"],
            )
        )


def downgrade() -> None:
    op.drop_table("mcp_servers")
