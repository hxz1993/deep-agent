"""normalize shipped MCP servers to builtin transport

Revision ID: 005
Revises: 004
Create Date: 2026-06-17

"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "005"
down_revision: Union[str, Sequence[str], None] = "004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ROOT = Path(__file__).resolve().parents[2]
MCPS_DIR = ROOT / "mcps"
SERVER_FILENAME = "server.py"


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _is_shipped_builtin(name: str) -> bool:
    return (MCPS_DIR / name / SERVER_FILENAME).is_file()


def _portable_builtin_config(config: dict[str, Any]) -> dict[str, Any]:
    portable: dict[str, Any] = {}
    if config.get("env"):
        portable["env"] = config["env"]
    if config.get("cwd"):
        portable["cwd"] = config["cwd"]
    return portable


def _should_convert(name: str, transport: str, config: dict[str, Any]) -> bool:
    if transport == "builtin":
        return False
    if not _is_shipped_builtin(name):
        return False
    if transport != "stdio":
        return False
    args = config.get("args") or []
    if len(args) == 1 and Path(str(args[0])).name == SERVER_FILENAME:
        return True
    return "command" not in config and "args" not in config


def upgrade() -> None:
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT name, transport, config FROM mcp_servers")
    ).mappings()

    for row in rows:
        name = row["name"]
        transport = row["transport"]
        config = row["config"]
        if isinstance(config, str):
            config = json.loads(config)
        if not _should_convert(name, transport, config):
            continue

        portable = _portable_builtin_config(config)
        if _is_postgresql():
            bind.execute(
                sa.text(
                    "UPDATE mcp_servers "
                    "SET transport = :transport, config = CAST(:config AS jsonb), updated_at = now() "
                    "WHERE name = :name"
                ),
                {
                    "name": name,
                    "transport": "builtin",
                    "config": json.dumps(portable),
                },
            )
        else:
            bind.execute(
                sa.text(
                    "UPDATE mcp_servers "
                    "SET transport = :transport, config = :config, updated_at = CURRENT_TIMESTAMP "
                    "WHERE name = :name"
                ),
                {
                    "name": name,
                    "transport": "builtin",
                    "config": json.dumps(portable),
                },
            )


def downgrade() -> None:
    import sys

    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT name, transport, config FROM mcp_servers")
    ).mappings()

    for row in rows:
        name = row["name"]
        if row["transport"] != "builtin" or not _is_shipped_builtin(name):
            continue

        config = row["config"]
        if isinstance(config, str):
            config = json.loads(config)

        server_file = (MCPS_DIR / name / SERVER_FILENAME).resolve()
        legacy = {
            "command": sys.executable,
            "args": [str(server_file)],
            **_portable_builtin_config(config),
        }
        if _is_postgresql():
            bind.execute(
                sa.text(
                    "UPDATE mcp_servers "
                    "SET transport = :transport, config = CAST(:config AS jsonb), updated_at = now() "
                    "WHERE name = :name"
                ),
                {
                    "name": name,
                    "transport": "stdio",
                    "config": json.dumps(legacy),
                },
            )
        else:
            bind.execute(
                sa.text(
                    "UPDATE mcp_servers "
                    "SET transport = :transport, config = :config, updated_at = CURRENT_TIMESTAMP "
                    "WHERE name = :name"
                ),
                {
                    "name": name,
                    "transport": "stdio",
                    "config": json.dumps(legacy),
                },
            )
