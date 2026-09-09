from __future__ import annotations

from typing import TYPE_CHECKING

from deep_agent.capabilities.general import build_native_tool_package
from deep_agent.capabilities.registry import ToolInfo, ToolPackage, ToolPackageRegistry
from deep_agent.options import OPTION
from loguru import logger

from deep_agent.sandbox.profile import ResolvedSandboxProfile
from deep_agent.sandbox.toolset import build_python_sandbox_tool_package

if TYPE_CHECKING:
    from deep_agent.db.stores.bundle import DataStores
    from deep_agent.sandbox.backend import PythonExecutionBackend

__all__ = [
    "ToolPackage",
    "ToolPackageRegistry",
    "ToolInfo",
    "build_registry",
]


def build_registry(
    *,
    stores: DataStores | None = None,
    sandbox_backend: PythonExecutionBackend | None = None,
    sandbox_profile: ResolvedSandboxProfile | None = None,
) -> ToolPackageRegistry:
    """Assemble the registry from static tool-package sources.

    ``stores`` is reserved for future immutable store-backed registration at
    startup. Mutable store config (e.g. MCP servers) is resolved at use time via
    ``capabilities.mcp.resolver``.
    """
    _ = stores
    from deep_agent.ptc.file_payload import build_code_mode_tool_package
    from deep_agent.web.package import build_web_tool_package

    registry = ToolPackageRegistry()

    registry.register(build_native_tool_package())
    registry.register(build_code_mode_tool_package())
    registry.register(build_web_tool_package())

    if sandbox_backend is not None:
        registry.register(
            build_python_sandbox_tool_package(
                sandbox_backend,
                timeout_sec=float(OPTION.agent.sandbox_timeout),
                sandbox_profile=sandbox_profile,
            )
        )
    logger.info("Registered tool packages: {}", ", ".join(registry.codes()))
    return registry
