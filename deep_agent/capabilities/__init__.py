from __future__ import annotations

from typing import TYPE_CHECKING

from deep_agent.capabilities.cursor_skills import load_cursor_skill_capabilities
from deep_agent.capabilities.native import build_native_capability
from deep_agent.capabilities.registry import Capability, CapabilityRegistry, ToolInfo
from deep_agent.capabilities.skills import load_skill_capabilities
from deep_agent.options import OPTION
from loguru import logger

if TYPE_CHECKING:
    from deep_agent.db.stores.bundle import DataStores

__all__ = [
    "Capability",
    "CapabilityRegistry",
    "ToolInfo",
    "build_registry",
]


def build_registry(*, stores: DataStores | None = None) -> CapabilityRegistry:
    """Assemble the registry from static capability sources.

    ``stores`` is reserved for future immutable store-backed registration at
    startup. Mutable store config (e.g. MCP servers) is resolved at use time via
    ``capabilities.mcp.resolver``.
    """
    _ = stores
    registry = CapabilityRegistry()

    registry.register(build_native_capability())

    for cap in load_skill_capabilities(OPTION.agent.skills_dir):
        registry.register(cap)

    for cap in load_cursor_skill_capabilities(OPTION.agent.cursor_skills_dir):
        registry.register(cap)

    logger.info("Registered capabilities: {}", ", ".join(registry.names()))
    return registry
