from __future__ import annotations

import unittest

from deep_agent.capabilities.mcp.db_loader import tool_package_from_row
from deep_agent.capabilities.registry import ToolPackage


class _McpRow:
    def __init__(self) -> None:
        self.name = "echo"
        self.transport = "url"
        self.config = {"url": "http://127.0.0.1:8765/mcp"}
        self.description = None


class McpToolPackageDefaultsTests(unittest.TestCase):
    def test_db_mcp_tool_package_uses_explicit_capability_code(self) -> None:
        with unittest.mock.patch(
            "deep_agent.capabilities.mcp.db_loader.build_mcp_toolset",
            return_value=object(),
        ):
            package = tool_package_from_row(_McpRow())  # type: ignore[arg-type]

        self.assertIsInstance(package, ToolPackage)
        self.assertEqual(package.code, "mcp:echo")
        self.assertEqual(package.category, "mcp")
        self.assertFalse(hasattr(package, "default_enabled"))


if __name__ == "__main__":
    unittest.main()
