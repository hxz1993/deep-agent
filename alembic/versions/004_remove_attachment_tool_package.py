"""Remove the obsolete attachment ToolPackage selection.

Revision ID: 004
Revises: 003
Create Date: 2026-08-12
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import text_json_type


revision: str = "004"
down_revision: Union[str, Sequence[str], None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_REMOVED_CODE = "builtin:attachment"


def _bind():
    return op.get_bind()


def _json_type():
    return text_json_type(_bind())


def _as_capabilities(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError(f"agents.capabilities contains an invalid value: {value!r}")
    return value


def _remove_attachment(values: list[str]) -> list[str]:
    return [value for value in values if value != _REMOVED_CODE]


def upgrade() -> None:
    bind = _bind()
    agents = sa.table(
        "agents",
        sa.column("id", sa.String(36)),
        sa.column("capabilities", _json_type()),
    )
    rows = bind.execute(sa.select(agents.c.id, agents.c.capabilities)).mappings().all()
    for row in rows:
        capabilities = _as_capabilities(row["capabilities"])
        if capabilities is None:
            continue
        updated = _remove_attachment(capabilities)
        if updated == capabilities:
            continue
        bind.execute(
            agents.update()
            .where(agents.c.id == row["id"])
            .values(
                capabilities=json.dumps(
                    updated,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
        )


def downgrade() -> None:
    # The old selection cannot be reconstructed without granting a tool package
    # to Agents that never selected it. Keep null/explicit/empty semantics intact.
    pass
