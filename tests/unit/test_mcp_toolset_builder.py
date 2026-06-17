from __future__ import annotations

import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, MagicMock, patch

from deep_agent.capabilities.mcp.toolset_builder import build_raw_stdio_mcp_toolset, resolve_mcp_init_timeout
from deep_agent.core.config import Settings, get_settings


class ResolveMcpInitTimeoutTests(unittest.TestCase):
    def test_uses_global_default(self) -> None:
        settings = replace(get_settings(), mcp_init_timeout=42.0)
        with patch("deep_agent.capabilities.mcp.toolset_builder.get_settings", return_value=settings):
            self.assertEqual(resolve_mcp_init_timeout({}), 42.0)

    def test_config_override_wins(self) -> None:
        settings = replace(get_settings(), mcp_init_timeout=42.0)
        with patch("deep_agent.capabilities.mcp.toolset_builder.get_settings", return_value=settings):
            self.assertEqual(resolve_mcp_init_timeout({"init_timeout": 15}), 15.0)


class BuildRawStdioMcpToolsetTests(unittest.TestCase):
    def test_passes_init_timeout_to_mcp_toolset(self) -> None:
        settings = Settings(mcp_init_timeout=25.0)
        config = {
            "command": "/usr/bin/python",
            "args": ["server.py"],
        }
        with (
            patch("deep_agent.capabilities.mcp.toolset_builder.get_settings", return_value=settings),
            patch("deep_agent.capabilities.mcp.toolset_builder.MCPToolset") as mcp_toolset_cls,
            patch("fastmcp.client.transports.StdioTransport") as stdio_cls,
        ):
            stdio_cls.return_value = MagicMock()
            build_raw_stdio_mcp_toolset("deepcube", config)
            _, kwargs = mcp_toolset_cls.call_args
            self.assertEqual(kwargs["init_timeout"], 25.0)


if __name__ == "__main__":
    unittest.main()
