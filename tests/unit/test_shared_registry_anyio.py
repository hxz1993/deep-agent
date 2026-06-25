from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path

from deep_agent.capabilities.mcp.shared_registry import McpSharedRegistry
from deep_agent.options import OPTION


class SharedRegistryEnsureTests(unittest.IsolatedAsyncioTestCase):
    async def test_ensure_server_text_tools(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            OPTION.agent.mcp_runtime_dir = tmpdir
            OPTION.agent.mcp_init_timeout = 15.0
            registry = McpSharedRegistry(runtime_dir=Path(tmpdir), connect_timeout=15.0)
            url = await registry.ensure_server("text_tools", {})
        self.assertIn("/mcp", url)


if __name__ == "__main__":
    unittest.main()
