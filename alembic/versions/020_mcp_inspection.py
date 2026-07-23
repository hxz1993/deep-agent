"""replace MCP resource snapshot with general inspection metadata

Revision ID: 020
Revises: 019
Create Date: 2026-07-23

"""

from __future__ import annotations

import json
from typing import Any
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import has_column, is_sqlite, text_json_type

revision: str = "020"
down_revision: Union[str, Sequence[str], None] = "019"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _load_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return {}
        return dict(value) if isinstance(value, dict) else {}
    return {}


def _inspection_from_resource_snapshot(
    raw: Any,
    *,
    transport: str,
) -> dict[str, Any]:
    if transport == "builtin":
        return {"status": "not_applicable"}

    resource = _load_json(raw)
    status = resource.get("status")
    if status not in {"supported", "unsupported", "unknown"}:
        status = "unknown"
    inspection: dict[str, Any] = {
        "status": "unknown",
        "checked_at": resource.get("checked_at"),
        "duration_ms": None,
        "previous_success_at": None,
        "server": {
            "name": None,
            "version": None,
            "protocol_version": None,
            "instructions_available": False,
        },
        "tools": {"status": "unknown", "count": 0, "error": None},
        "resources": {
            "status": status,
            "count": int(resource.get("resource_count") or 0),
            "template_count": int(resource.get("template_count") or 0),
            "error": None,
        },
        "error": None,
    }
    if resource.get("error"):
        inspection["resources"]["error"] = {
            "stage": "legacy_resource_probe",
            "type": "LegacyProbeError",
            "message": "The previous Resource probe did not complete successfully",
        }
    return inspection


def _resource_snapshot_from_inspection(raw: Any) -> dict[str, Any]:
    inspection = _load_json(raw)
    resources = _load_json(inspection.get("resources"))
    status = resources.get("status")
    if status not in {"supported", "unsupported", "unknown"}:
        status = "unknown"
    error = resources.get("error")
    return {
        "status": status,
        "resource_count": int(resources.get("count") or 0),
        "template_count": int(resources.get("template_count") or 0),
        "checked_at": inspection.get("checked_at"),
        "error": (
            "Resource detection failed"
            if error or inspection.get("status") in {"unreachable", "degraded"}
            else None
        ),
    }


def _drop_column(bind, table_name: str, column_name: str) -> None:
    if not has_column(bind, table_name, column_name):
        return
    if is_sqlite(bind):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_column(column_name)
    else:
        op.drop_column(table_name, column_name)


def upgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "mcp_servers", "inspection"):
        op.add_column(
            "mcp_servers",
            sa.Column("inspection", text_json_type(bind), nullable=True),
        )

    if has_column(bind, "mcp_servers", "resource_capabilities"):
        rows = bind.execute(
            sa.text(
                "SELECT name, transport, resource_capabilities FROM mcp_servers"
            )
        ).mappings()
        for row in rows:
            inspection = _inspection_from_resource_snapshot(
                row["resource_capabilities"],
                transport=row["transport"],
            )
            bind.execute(
                sa.text(
                    "UPDATE mcp_servers SET inspection = :inspection WHERE name = :name"
                ),
                {
                    "name": row["name"],
                    "inspection": json.dumps(
                        inspection,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            )
        _drop_column(bind, "mcp_servers", "resource_capabilities")


def downgrade() -> None:
    bind = op.get_bind()
    if not has_column(bind, "mcp_servers", "resource_capabilities"):
        op.add_column(
            "mcp_servers",
            sa.Column("resource_capabilities", text_json_type(bind), nullable=True),
        )

    if has_column(bind, "mcp_servers", "inspection"):
        rows = bind.execute(
            sa.text("SELECT name, inspection FROM mcp_servers")
        ).mappings()
        for row in rows:
            snapshot = _resource_snapshot_from_inspection(row["inspection"])
            bind.execute(
                sa.text(
                    "UPDATE mcp_servers "
                    "SET resource_capabilities = :snapshot WHERE name = :name"
                ),
                {
                    "name": row["name"],
                    "snapshot": json.dumps(
                        snapshot,
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                },
            )
        _drop_column(bind, "mcp_servers", "inspection")
