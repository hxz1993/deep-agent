from __future__ import annotations

from deep_agent.capabilities.mcp.resolver import load_mcp_tool_packages
from deep_agent.capabilities.registry import ToolPackageRegistry
from deep_agent.capabilities.ui.registry import runnable_ui_tool_package_codes
from deep_agent.db.stores.bundle import DataStores


async def selectable_tool_package_codes(
    registry: ToolPackageRegistry,
    stores: DataStores,
) -> set[str]:
    codes = set(registry.codes())
    codes.update(
        package.code for package in await load_mcp_tool_packages(stores)
    )
    codes.update(await runnable_ui_tool_package_codes(stores))
    return codes


async def invalid_tool_package_codes(
    codes: list[str] | None,
    registry: ToolPackageRegistry,
    stores: DataStores,
) -> list[str]:
    if not codes:
        return []
    available = await selectable_tool_package_codes(registry, stores)
    return sorted({code for code in codes if code not in available})
