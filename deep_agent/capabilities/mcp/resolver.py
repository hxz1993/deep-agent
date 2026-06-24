from __future__ import annotations

from typing import Any

from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.mcp.builtin import load_builtin_mcp_capabilities
from deep_agent.capabilities.mcp.db_loader import capabilities_from_rows
from deep_agent.capabilities.registry import Capability
from deep_agent.core.paths import DEEP_AGENT_ROOT
from deep_agent.db.stores.bundle import DataStores


def _describe_capability(cap: Capability) -> dict[str, Any]:
    return {
        "name": cap.name,
        "kind": cap.kind,
        "description": cap.description,
        "default_enabled": cap.default_enabled,
        "tools": [{"name": t.name, "description": t.description} for t in cap.tools],
    }


async def load_mcp_capabilities(stores: DataStores) -> list[Capability]:
    """Load enabled MCP servers from the database with builtin fallback."""
    if stores.mcp is None:
        return []

    rows = await stores.mcp.list_all()
    caps = capabilities_from_rows(rows)

    db_names = {row.name for row in rows if row.enabled}
    mcps_dir = DEEP_AGENT_ROOT / "mcps"
    for builtin in load_builtin_mcp_capabilities(str(mcps_dir)):
        short_name = builtin.name.removeprefix("mcp:")
        if short_name not in db_names:
            caps.append(builtin)

    return caps


def select_mcp_capabilities(caps: list[Capability], spec: AgentSpec) -> list[Capability]:
    """Mirror ``CapabilityRegistry.toolsets`` selection semantics for MCP caps."""
    if spec.capabilities is None:
        return [c for c in caps if c.default_enabled]
    return [c for c in caps if c.name in spec.capabilities]


async def resolve_mcp_toolsets(stores: DataStores, spec: AgentSpec) -> list[Any]:
    caps = select_mcp_capabilities(await load_mcp_capabilities(stores), spec)
    return [c.toolset for c in caps]


async def describe_mcp_capabilities(stores: DataStores) -> list[dict[str, Any]]:
    return [_describe_capability(cap) for cap in await load_mcp_capabilities(stores)]
