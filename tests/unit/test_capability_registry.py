from __future__ import annotations

import unittest
from unittest.mock import MagicMock

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.capabilities.attachments import build_attachment_capability
from deep_agent.capabilities.native import build_native_capability
from deep_agent.capabilities.registry import Capability, CapabilityRegistry


class CapabilityRegistryToolsetsTests(unittest.TestCase):
    def test_empty_capabilities_returns_no_toolsets(self) -> None:
        registry = CapabilityRegistry()
        registry.register(
            Capability(name="native", kind="native", toolset=object(), default_enabled=True)
        )
        registry.register(
            Capability(
                name="mcp:echo",
                kind="mcp",
                toolset=object(),
                default_enabled=False,
            )
        )

        toolsets = registry.toolsets([])
        self.assertEqual(toolsets, [])

    def test_null_capabilities_excludes_non_default_mcp(self) -> None:
        registry = CapabilityRegistry()
        native_toolset = object()
        mcp_toolset = object()
        registry.register(
            Capability(name="native", kind="native", toolset=native_toolset, default_enabled=True)
        )
        registry.register(
            Capability(
                name="mcp:echo",
                kind="mcp",
                toolset=mcp_toolset,
                default_enabled=False,
            )
        )

        toolsets = registry.toolsets(None)
        self.assertEqual(toolsets, [native_toolset])

    def test_explicit_mcp_capability_is_included(self) -> None:
        registry = CapabilityRegistry()
        mcp_toolset = MagicMock()
        registry.register(
            Capability(
                name="mcp:echo",
                kind="mcp",
                toolset=mcp_toolset,
                default_enabled=False,
            )
        )

        toolsets = registry.toolsets(["mcp:echo"])
        self.assertEqual(toolsets, [mcp_toolset])

    def test_attachment_is_opt_in_for_null_empty_and_explicit_selection(self) -> None:
        registry = CapabilityRegistry()
        attachment = build_attachment_capability()
        registry.register(attachment)

        self.assertEqual(registry.toolsets(None), [])
        self.assertEqual(registry.toolsets([]), [])
        self.assertEqual(registry.toolsets(["attachment"]), [attachment.toolset])

        described = registry.describe()
        self.assertEqual(described[0]["name"], "attachment")
        self.assertEqual(described[0]["kind"], "attachment")
        self.assertEqual(described[0]["label"], "附件读取")
        self.assertEqual(described[0]["source"], "builtin")
        self.assertFalse(described[0]["default_enabled"])


class BuiltinCapabilityMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_only_registers_current_time(self) -> None:
        native = build_native_capability()
        tools = await native.toolset.get_tools(
            RunContext(deps=None, model=TestModel(), usage=RunUsage())
        )

        self.assertEqual(native.label, "通用工具")
        self.assertEqual(native.source, "builtin")
        self.assertTrue(native.default_enabled)
        self.assertEqual(set(tools), {"get_current_time"})
        self.assertEqual({item.name for item in native.tools}, {"get_current_time"})
        self.assertNotIn("http_get", tools)

    async def test_attachment_remains_opt_in_builtin_package(self) -> None:
        attachment = build_attachment_capability()

        self.assertEqual(attachment.label, "附件读取")
        self.assertEqual(attachment.source, "builtin")
        self.assertFalse(attachment.default_enabled)
        self.assertEqual(
            {item.name for item in attachment.tools},
            {"read_text_attachment", "read_docx", "read_xlsx"},
        )


if __name__ == "__main__":
    unittest.main()
