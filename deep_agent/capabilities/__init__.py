from __future__ import annotations

from typing import TYPE_CHECKING

from deep_agent.capabilities.attachments import build_attachment_capability
from deep_agent.capabilities.native import build_native_capability
from deep_agent.capabilities.registry import Capability, CapabilityRegistry, ToolInfo
from deep_agent.capabilities.skills import load_skill_capabilities
from deep_agent.options import OPTION
from loguru import logger

from deep_agent.sandbox.profile import ResolvedSandboxProfile
from deep_agent.sandbox.toolset import build_python_sandbox_capability

if TYPE_CHECKING:
    from deep_agent.db.stores.bundle import DataStores
    from deep_agent.sandbox.backend import PythonExecutionBackend

__all__ = [
    "Capability",
    "CapabilityRegistry",
    "ToolInfo",
    "build_registry",
]


def build_registry(
    *,
    stores: DataStores | None = None,
    sandbox_backend: PythonExecutionBackend | None = None,
    sandbox_profile: ResolvedSandboxProfile | None = None,
) -> CapabilityRegistry:
    """Assemble the registry from static capability sources.

    ``stores`` is reserved for future immutable store-backed registration at
    startup. Mutable store config (e.g. MCP servers) is resolved at use time via
    ``capabilities.mcp.resolver``.
    """
    _ = stores
    registry = CapabilityRegistry()

    registry.register(build_native_capability())
    registry.register(build_attachment_capability())

    if sandbox_backend is not None:
        registry.register(
            build_python_sandbox_capability(
                sandbox_backend,
                timeout_sec=float(OPTION.agent.sandbox_timeout),
                sandbox_profile=sandbox_profile,
            )
        )

    for cap in load_skill_capabilities(OPTION.agent.skills_dir):
        registry.register(cap)

    logger.info("Registered capabilities: {}", ", ".join(registry.names()))
    return registry
