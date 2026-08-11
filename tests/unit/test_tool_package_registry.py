from __future__ import annotations

import unittest
from datetime import datetime
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
                toolset=object(),
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
                toolset=general,
                default_enabled=True,
            )
        )
        registry.register(
            ToolPackage(
                code="mcp:echo",
                category="mcp",
                toolset=mcp,
                default_enabled=False,
            )
        )
        self.assertEqual(registry.toolsets(None), [general])
        self.assertEqual(registry.toolsets(["mcp:echo"]), [mcp])

    def test_one_package_contributes_exactly_one_toolset(self) -> None:
        registry = ToolPackageRegistry()
        toolset = object()
        registry.register(
            ToolPackage(
                code="builtin:general",
                category="builtin",
                toolset=toolset,
            )
        )
        self.assertEqual(registry.toolsets(None), [toolset])
        self.assertFalse(hasattr(registry.get("builtin:general"), "toolsets"))

    def test_attachment_is_opt_in(self) -> None:
        registry = ToolPackageRegistry()
        attachment = build_attachment_tool_package()
        registry.register(attachment)
        self.assertEqual(registry.toolsets(None), [])
        self.assertEqual(registry.toolsets([]), [])
        self.assertEqual(
            registry.toolsets(["builtin:attachment"]), [attachment.toolset]
        )
        described = registry.describe()[0]
        self.assertEqual(described["code"], "builtin:attachment")
        self.assertEqual(described["category"], "builtin")
        self.assertEqual(described["tool_count"], 3)
        self.assertEqual(described["disclosure_mode"], "conditional")
        self.assertEqual(
            described["disclosure_condition"],
            "session_has_attachment",
        )


class BuiltinToolPackageMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_general_package_contains_current_time(self) -> None:
        package = build_native_tool_package()
        context = RunContext(deps=None, model=TestModel(), usage=RunUsage())
        tools = await package.toolset.get_tools(context)
        self.assertEqual(package.code, "builtin:general")
        self.assertEqual(package.category, "builtin")
        self.assertTrue(package.default_enabled)
        self.assertEqual(set(tools), {"get_current_time"})
        current_time = await tools["get_current_time"].call_func({}, context)
        self.assertIsNotNone(datetime.fromisoformat(current_time))

    async def test_attachment_remains_opt_in(self) -> None:
        package = build_attachment_tool_package()
        self.assertFalse(package.default_enabled)
        self.assertEqual(
            {item.name for item in package.tools},
            {"read_text_attachment", "read_docx", "read_xlsx"},
        )


class PythonSandboxPackageTests(unittest.TestCase):
    def test_sandbox_has_independent_package_code(self) -> None:
        backend = MagicMock()
        backend.health = AsyncMock()
        backend.aclose = AsyncMock()
        package = build_python_sandbox_tool_package(backend, timeout_sec=30.0)
        self.assertEqual(package.code, "builtin:sandbox")
        self.assertFalse(package.default_enabled)
        self.assertEqual(
            {item.name for item in package.tools},
            {"execute_python"},
        )

    def test_registry_keeps_general_and_sandbox_separate(self) -> None:
        backend = MagicMock()
        registry = build_registry(sandbox_backend=backend)
        general = registry.get("builtin:general")
        sandbox = registry.get("builtin:sandbox")
        self.assertEqual(
            {item.name for item in general.tools},
            {"get_current_time"},
        )
        self.assertEqual(
            {item.name for item in sandbox.tools},
            {"execute_python"},
        )
        default_toolsets = registry.toolsets(None)
        self.assertIn(general.toolset, default_toolsets)
        self.assertNotIn(sandbox.toolset, default_toolsets)
        self.assertEqual(
            registry.toolsets(["builtin:sandbox"]),
            [sandbox.toolset],
        )

    def test_registry_general_only_contains_time_without_backend(self) -> None:
        registry = build_registry()
        general = registry.get("builtin:general")
        self.assertEqual({item.name for item in general.tools}, {"get_current_time"})


if __name__ == "__main__":
    unittest.main()
