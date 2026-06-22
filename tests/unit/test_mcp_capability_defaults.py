from __future__ import annotations

import unittest

from deep_agent.capabilities.mcp.db_loader import capability_from_row
from deep_agent.capabilities.registry import Capability


class _McpRow:
    def __init__(self) -> None:
        self.name = "echo"
        self.transport = "stdio"
        self.config = {"command": "python", "args": ["server.py"]}
        self.description = None


class McpCapabilityDefaultsTests(unittest.TestCase):
    def test_db_mcp_capability_is_not_default_enabled(self) -> None:
        with unittest.mock.patch(
            "deep_agent.capabilities.mcp.db_loader.build_mcp_toolset",
            return_value=object(),
        ):
            cap = capability_from_row(_McpRow())  # type: ignore[arg-type]

        self.assertIsInstance(cap, Capability)
        self.assertFalse(cap.default_enabled)


if __name__ == "__main__":
    unittest.main()
