from __future__ import annotations

import unittest
from unittest.mock import AsyncMock

from deep_agent.capabilities.mcp.toolset_builder import build_raw_stdio_mcp_toolset, resolve_mcp_init_timeout
from deep_agent.options import OPTION


class ResolveMcpInitTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_timeout = OPTION.agent.mcp_init_timeout

    def tearDown(self) -> None:
        OPTION.agent.mcp_init_timeout = float(self._original_timeout)

    def test_uses_global_default(self) -> None:
        OPTION.agent.mcp_init_timeout = 42.0
        self.assertEqual(resolve_mcp_init_timeout({}), 42.0)

    def test_config_override_wins(self) -> None:
        OPTION.agent.mcp_init_timeout = 42.0
        self.assertEqual(resolve_mcp_init_timeout({"init_timeout": 15}), 15.0)


class BuildRawStdioMcpToolsetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_timeout = OPTION.agent.mcp_init_timeout

    def tearDown(self) -> None:
        OPTION.agent.mcp_init_timeout = float(self._original_timeout)

    def test_passes_init_timeout_to_mcp_toolset(self) -> None:
        OPTION.agent.mcp_init_timeout = 25.0
        config = {
            "command": "/usr/bin/python",
            "args": ["server.py"],
        }
        with (
            unittest.mock.patch("deep_agent.capabilities.mcp.toolset_builder.MCPToolset") as mcp_toolset_cls,
            unittest.mock.patch("deep_agent.capabilities.mcp.toolset_builder.SerializingToolset") as serializing_cls,
            unittest.mock.patch("fastmcp.client.transports.StdioTransport") as stdio_cls,
        ):
            stdio_cls.return_value = unittest.mock.MagicMock()
            mcp_toolset_cls.return_value = unittest.mock.MagicMock()
            serializing_cls.side_effect = lambda wrapped: wrapped
            build_raw_stdio_mcp_toolset("deepcube", config)
            _, kwargs = mcp_toolset_cls.call_args
            self.assertEqual(kwargs["init_timeout"], 25.0)
            serializing_cls.assert_called_once()


if __name__ == "__main__":
    unittest.main()
