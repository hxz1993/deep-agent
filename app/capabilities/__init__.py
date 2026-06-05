from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.capabilities.cursor_skills import load_cursor_skill_capabilities
from app.capabilities.mcp.db_loader import capabilities_from_rows
from app.capabilities.native import build_native_capability
from app.capabilities.registry import Capability, CapabilityRegistry, ToolInfo
from app.capabilities.skills import load_skill_capabilities
from app.core.config import Settings
from app.core.logging import get_logger
from app.db.repositories.mcp import McpServerRepository

logger = get_logger(__name__)

__all__ = [
    "Capability",
    "CapabilityRegistry",
    "ToolInfo",
    "build_registry",
    "load_and_register_mcp",
]


def build_registry(settings: Settings) -> CapabilityRegistry:
    """Assemble the registry from native, skill, and cursor-skill sources."""
    registry = CapabilityRegistry()

    registry.register(build_native_capability())

    for cap in load_skill_capabilities(settings.skills_dir):
        registry.register(cap)

    for cap in load_cursor_skill_capabilities(settings.cursor_skills_dir):
        registry.register(cap)

    logger.info("Registered capabilities: %s", ", ".join(registry.names()))
    return registry


async def load_and_register_mcp(registry: CapabilityRegistry, db: AsyncSession) -> None:
    """Load enabled MCP servers from the database and hot-reload registry entries."""
    repo = McpServerRepository()
    rows = await repo.list_all(db)
    caps = capabilities_from_rows(rows)
    registry.replace_by_kind("mcp", caps)
    logger.info(
        "Registered MCP capabilities from database: %s",
        ", ".join(c.name for c in caps) or "(none)",
    )
