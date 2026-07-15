"""seed deepcube dim init concurrency env default

Revision ID: 016
Revises: 015
Create Date: 2026-07-15

"""

from __future__ import annotations

import json
from typing import Any, Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "016"
down_revision: Union[str, Sequence[str], None] = "015"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

ENV_KEY = "DEEPCUBE_MCP_DIM_INIT_MAX_CONCURRENCY"
DEFAULT_VALUE = "4"


def _is_postgresql() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def _load_config(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        return json.loads(raw)
    return {}


def _update_deepcube_config(config: dict[str, Any]) -> None:
    bind = op.get_bind()
    payload = json.dumps(config)
    if _is_postgresql():
        bind.execute(
            sa.text(
                "UPDATE mcp_servers "
                "SET config = CAST(:config AS jsonb), updated_at = now() "
                "WHERE name = 'deepcube'"
            ),
            {"config": payload},
        )
        return

    bind.execute(
        sa.text(
            "UPDATE mcp_servers "
            "SET config = :config, updated_at = CURRENT_TIMESTAMP "
            "WHERE name = 'deepcube'"
        ),
        {"config": payload},
    )


def upgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT config FROM mcp_servers WHERE name = 'deepcube'")
    ).mappings().first()
    if row is None:
        return

    config = _load_config(row["config"])
    env = dict(config.get("env") or {})
    if ENV_KEY in env:
        return

    env[ENV_KEY] = DEFAULT_VALUE
    config["env"] = env
    _update_deepcube_config(config)


def downgrade() -> None:
    bind = op.get_bind()
    row = bind.execute(
        sa.text("SELECT config FROM mcp_servers WHERE name = 'deepcube'")
    ).mappings().first()
    if row is None:
        return

    config = _load_config(row["config"])
    env = dict(config.get("env") or {})
    if ENV_KEY not in env:
        return

    env.pop(ENV_KEY, None)
    if env:
        config["env"] = env
    else:
        config.pop("env", None)

    _update_deepcube_config(config)
