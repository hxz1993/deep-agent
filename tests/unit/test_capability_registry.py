from __future__ import annotations

import unittest
from unittest.mock import MagicMock

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


if __name__ == "__main__":
    unittest.main()
