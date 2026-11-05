from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from deep_agent.capabilities import build_registry
from deep_agent.capabilities.registry import ToolPackage, ToolPackageRegistry
from deep_agent.sandbox.toolset import build_python_sandbox_tool_package


class ToolPackageRegistryTests(unittest.TestCase):
    def test_empty_selection_returns_no_toolsets(self) -> None:
        registry = ToolPackageRegistry()
        registry.register(
            ToolPackage(
                code="builtin:fixture",
                category="builtin",
                toolset=object(),
            )
        )
        self.assertEqual(registry.toolsets([]), [])

    def test_null_selection_returns_no_packages(self) -> None:
        registry = ToolPackageRegistry()
        general = object()
        mcp = object()
        registry.register(
            ToolPackage(
                code="builtin:fixture",
                category="builtin",
                toolset=general,
            )
        )
        registry.register(
            ToolPackage(
                code="mcp:echo",
                category="mcp",
                toolset=mcp,
            )
        )
        self.assertEqual(registry.toolsets(None), [])
        self.assertEqual(registry.toolsets(["mcp:echo"]), [mcp])

    def test_one_package_contributes_exactly_one_toolset(self) -> None:
        registry = ToolPackageRegistry()
        toolset = object()
        registry.register(
            ToolPackage(
                code="builtin:fixture",
                category="builtin",
                toolset=toolset,
            )
        )
        self.assertEqual(registry.toolsets(None), [])
        self.assertEqual(registry.toolsets(["builtin:fixture"]), [toolset])
        self.assertFalse(hasattr(registry.get("builtin:fixture"), "toolsets"))


class PythonSandboxPackageTests(unittest.TestCase):
    def test_sandbox_has_independent_package_code(self) -> None:
        backend = MagicMock()
        backend.health = AsyncMock()
        backend.aclose = AsyncMock()
        package = build_python_sandbox_tool_package(backend, timeout_sec=30.0)
        self.assertEqual(package.code, "builtin:sandbox")
        self.assertEqual(
            {item.name for item in package.tools},
            {"execute_python"},
        )

    def test_registry_only_contains_configurable_packages(self) -> None:
        registry = build_registry(sandbox_backend=MagicMock())
        sandbox = registry.get("builtin:sandbox")
        self.assertEqual({item.name for item in sandbox.tools}, {"execute_python"})
        self.assertNotIn("builtin:general", registry.codes())
        self.assertFalse(any(code.startswith("runtime:") for code in registry.codes()))
        self.assertEqual(registry.toolsets(None), [])
        self.assertEqual(registry.toolsets(["builtin:general"]), [])
        self.assertEqual(registry.toolsets(["builtin:sandbox"]), [sandbox.toolset])

    def test_registry_without_backend_has_no_general_or_sandbox(self) -> None:
        registry = build_registry()
        self.assertNotIn("builtin:general", registry.codes())
        self.assertNotIn("builtin:sandbox", registry.codes())


if __name__ == "__main__":
    unittest.main()
