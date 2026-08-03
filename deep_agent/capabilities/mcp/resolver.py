from __future__ import annotations

from typing import Any

from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.mcp.builtin import load_builtin_mcp_tool_packages
from deep_agent.capabilities.mcp.db_loader import tool_packages_from_rows
from deep_agent.capabilities.registry import ToolPackage, describe_tool_package
from deep_agent.core.paths import DEEP_AGENT_ROOT
from deep_agent.db.models import McpServer
from deep_agent.db.stores.bundle import DataStores


def effective_mcp_capability_names(spec: AgentSpec, packages: list[ToolPackage]) -> set[str]:
    """Short MCP server names (no ``mcp:`` prefix) selected for ``spec``."""
    return {
        package.code.removeprefix("mcp:")
        for package in select_mcp_tool_packages(packages, spec)
        if package.code.startswith("mcp:")
    }


async def load_mcp_rows(stores: DataStores) -> list[McpServer]:
    return await stores.mcp.list_all()


async def load_mcp_tool_packages(
    stores: DataStores,
    *,
    rows: list[McpServer] | None = None,
) -> list[ToolPackage]:
    """Load enabled MCP servers from the database with builtin fallback."""
    if rows is None:
        rows = await stores.mcp.list_all()
    packages = tool_packages_from_rows(rows)

    db_names = {row.name for row in rows if row.enabled}
    mcps_dir = DEEP_AGENT_ROOT / "mcps"
    for builtin in load_builtin_mcp_tool_packages(str(mcps_dir)):
        short_name = builtin.code.removeprefix("mcp:")
        if short_name not in db_names:
            packages.append(builtin)

    return packages


def select_mcp_tool_packages(
    packages: list[ToolPackage], spec: AgentSpec
) -> list[ToolPackage]:
    """Mirror ``ToolPackageRegistry.toolsets`` selection semantics for MCP packages."""
    if spec.capabilities is None:
        return [package for package in packages if package.default_enabled]
    return [package for package in packages if package.code in spec.capabilities]


async def resolve_mcp_toolsets(
    stores: DataStores,
    spec: AgentSpec,
    *,
    mcp_rows: list[McpServer] | None = None,
) -> list[Any]:
    packages = select_mcp_tool_packages(
        await load_mcp_tool_packages(stores, rows=mcp_rows), spec
    )
    return [toolset for package in packages for toolset in package.toolsets]


async def describe_mcp_tool_packages(
    stores: DataStores,
    *,
    mcp_rows: list[McpServer] | None = None,
) -> list[dict[str, Any]]:
    return [
        describe_tool_package(package)
        for package in await load_mcp_tool_packages(stores, rows=mcp_rows)
    ]
