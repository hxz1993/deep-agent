from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.capabilities import build_registry
from deep_agent.capabilities.attachments import build_attachment_tool_package
from deep_agent.capabilities.native import build_native_tool_package
from deep_agent.capabilities.registry import ToolPackage, ToolPackageRegistry
from deep_agent.sandbox.toolset import build_python_sandbox_tool_package


class ToolPackageRegistryTests(unittest.TestCase):
    def test_empty_selection_returns_no_toolsets(self) -> None:
        registry = ToolPackageRegistry()
        registry.register(
            ToolPackage(
                code="builtin:general",
                category="builtin",
                toolsets=[object()],
                default_enabled=True,
            )
        )
        self.assertEqual(registry.toolsets([]), [])

    def test_null_selection_only_returns_default_packages(self) -> None:
        registry = ToolPackageRegistry()
        general = object()
        mcp = object()
        registry.register(
            ToolPackage(
                code="builtin:general",
                category="builtin",
                toolsets=[general],
                default_enabled=True,
            )
        )
        registry.register(
            ToolPackage(
                code="mcp:echo",
                category="mcp",
                toolsets=[mcp],
                default_enabled=False,
            )
        )
        self.assertEqual(registry.toolsets(None), [general])
        self.assertEqual(registry.toolsets(["mcp:echo"]), [mcp])

    def test_one_package_can_contribute_multiple_toolsets(self) -> None:
        registry = ToolPackageRegistry()
        first, second = object(), object()
        registry.register(
            ToolPackage(
                code="builtin:general",
                category="builtin",
                toolsets=[first, second],
            )
        )
        self.assertEqual(registry.toolsets(None), [first, second])

    def test_attachment_is_opt_in(self) -> None:
        registry = ToolPackageRegistry()
        attachment = build_attachment_tool_package()
        registry.register(attachment)
        self.assertEqual(registry.toolsets(None), [])
        self.assertEqual(registry.toolsets([]), [])
        self.assertEqual(
            registry.toolsets(["builtin:attachment"]), attachment.toolsets
        )
        described = registry.describe()[0]
        self.assertEqual(described["code"], "builtin:attachment")
        self.assertEqual(described["category"], "builtin")
        self.assertEqual(described["tool_count"], 3)


class BuiltinToolPackageMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_general_package_contains_current_time(self) -> None:
        package = build_native_tool_package()
        tools = await package.toolsets[0].get_tools(
            RunContext(deps=None, model=TestModel(), usage=RunUsage())
        )
        self.assertEqual(package.code, "builtin:general")
        self.assertEqual(package.category, "builtin")
        self.assertTrue(package.default_enabled)
        self.assertEqual(set(tools), {"get_current_time"})

    async def test_attachment_remains_opt_in(self) -> None:
        package = build_attachment_tool_package()
        self.assertFalse(package.default_enabled)
        self.assertEqual(
            {item.name for item in package.tools},
            {"read_text_attachment", "read_docx", "read_xlsx"},
        )


class PythonSandboxPackageTests(unittest.TestCase):
    def test_sandbox_fragment_uses_general_package_code(self) -> None:
        backend = MagicMock()
        backend.health = AsyncMock()
        backend.aclose = AsyncMock()
        package = build_python_sandbox_tool_package(backend, timeout_sec=30.0)
        self.assertEqual(package.code, "builtin:general")
        self.assertEqual({item.name for item in package.tools}, {"execute_python"})

    def test_registry_merges_sandbox_into_general_when_backend_present(self) -> None:
        backend = MagicMock()
        registry = build_registry(sandbox_backend=backend)
        general = registry.get("builtin:general")
        self.assertEqual(
            {item.name for item in general.tools},
            {"get_current_time", "execute_python"},
        )
        self.assertEqual(len(general.toolsets), 2)

    def test_registry_general_only_contains_time_without_backend(self) -> None:
        registry = build_registry()
        general = registry.get("builtin:general")
        self.assertEqual({item.name for item in general.tools}, {"get_current_time"})


if __name__ == "__main__":
    unittest.main()
