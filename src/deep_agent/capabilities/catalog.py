from __future__ import annotations

from deep_agent.capabilities.mcp.resolver import load_mcp_tool_packages
from deep_agent.capabilities.codes import canonical_tool_package_codes
from deep_agent.capabilities.registry import ToolPackageRegistry
from deep_agent.capabilities.ui.registry import runnable_ui_tool_package_codes
from deep_agent.db.stores.bundle import DataStores
from deep_agent.remote_agents.contracts import CONNECTION_PREFIX


async def selectable_tool_package_codes(
    registry: ToolPackageRegistry,
    stores: DataStores,
) -> set[str]:
    codes = set(registry.codes())
    codes.update(
        package.code for package in await load_mcp_tool_packages(stores)
    )
    codes.update(await runnable_ui_tool_package_codes(stores))
    if stores.remote_agent is not None:
        codes.update(CONNECTION_PREFIX + row.id for row in await stores.remote_agent.list())
    return codes


async def invalid_tool_package_codes(
    codes: list[str] | None,
    registry: ToolPackageRegistry,
    stores: DataStores,
) -> list[str]:
    if not codes:
        return []
    available = await selectable_tool_package_codes(registry, stores)
    return sorted(
        {code for code in canonical_tool_package_codes(codes) if code not in available}
    )
