from __future__ import annotations

from fastapi import APIRouter

from deep_agent.capabilities.mcp.builtin import load_builtin_mcp_tool_packages
from deep_agent.capabilities.registry import describe_tool_package
from deep_agent.capabilities.ui.builtin import build_builtin_ui_tool_package
from deep_agent.capabilities.ui.external import build_args_validator
from deep_agent.capabilities.ui.types import UIToolRecord
from deep_agent.core.context import AppContextDep
from deep_agent.core.paths import DEEP_AGENT_ROOT
from deep_agent.schemas.chat import ToolPackageDescription
from deep_agent.schemas.mcp import McpInspection

router = APIRouter()


def _ui_tool_payload(record: UIToolRecord) -> dict:
    return {
        "name": record.name,
        "display_name": str((record.metadata or {}).get("display_name") or record.name),
        "description": record.description,
        "execution_type": "external",
        "enabled": record.enabled,
    }


@router.get("/tools", response_model=list[ToolPackageDescription])
async def list_tool_packages(ctx: AppContextDep) -> list[ToolPackageDescription]:
    """Return the product tool-package directory used by Agent configuration."""
    payloads = list(ctx.registry.describe())

    mcp_rows = await ctx.stores.mcp.list_all()
    db_names = {row.name for row in mcp_rows}
    for row in mcp_rows:
        inspection = McpInspection.from_value(row.inspection, transport=row.transport)
        catalog_status = (
            "count_only"
            if row.transport == "builtin" or inspection.tools.snapshot_status == "ready"
            else inspection.tools.snapshot_status
        )
        payloads.append(
            {
                "code": f"mcp:{row.name}",
                "category": "mcp",
                "label": row.name,
                "source": "builtin" if row.transport == "builtin" else "database",
                "description": row.description or f"MCP 服务 {row.name}",
                "enabled": row.enabled,
                "default_enabled": False,
                "tool_count": inspection.tools.count,
                "tool_catalog_status": catalog_status,
                "tools": [],
                "disclosure_mode": (
                    "search" if inspection.tools.count > 3 else "direct"
                ),
                "preselect_limit": 3 if inspection.tools.count > 3 else None,
                "search_result_limit": 5 if inspection.tools.count > 3 else None,
            }
        )
    for package in load_builtin_mcp_tool_packages(str(DEEP_AGENT_ROOT / "mcps")):
        if package.code.removeprefix("mcp:") in db_names:
            continue
        payload = describe_tool_package(package)
        payload["tool_catalog_status"] = "count_only"
        if payload["tool_count"] > 3:
            payload.update(
                disclosure_mode="search",
                preselect_limit=3,
                search_result_limit=5,
            )
        payloads.append(payload)

    payloads.append(describe_tool_package(build_builtin_ui_tool_package()))

    manual_rows = await ctx.stores.ui_tool.list_all()
    manual_by_package: dict[str, list[UIToolRecord]] = {}
    for row in manual_rows:
        if row.source != "manual":
            continue
        record = UIToolRecord.from_row(row)
        manual_by_package.setdefault(record.package_code, []).append(record)
    for package in await ctx.stores.ui_tool_package.list_all():
        records = sorted(manual_by_package.get(package.code, []), key=lambda item: item.name)
        enabled_records = [record for record in records if record.enabled]
        invalid_enabled_count = 0
        for record in enabled_records:
            try:
                build_args_validator(record)
            except ValueError:
                invalid_enabled_count += 1
        if not enabled_records:
            catalog_status = "empty"
        elif invalid_enabled_count == len(enabled_records):
            catalog_status = "invalid"
        elif invalid_enabled_count:
            catalog_status = "partial"
        else:
            catalog_status = "complete"
        payloads.append(
            {
                "code": package.code,
                "category": "ui",
                "label": package.label,
                "source": package.source,
                "description": package.description or "",
                "enabled": package.enabled,
                "default_enabled": False,
                "tool_count": len(records),
                "tool_catalog_status": catalog_status,
                "tools": [_ui_tool_payload(record) for record in records],
            }
        )

    payloads.sort(key=lambda item: (item["category"], item["code"]))
    return [ToolPackageDescription(**payload) for payload in payloads]
