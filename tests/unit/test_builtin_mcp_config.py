from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from deep_agent.capabilities.mcp.builtin_config import (
    list_builtin_server_names,
    normalize_mcp_config,
    resolve_builtin_spawn_config,
)
from tests.helpers.builtin_mcp_fixture import TEST_MCPS_DIR


class BuiltinMcpConfigTests(unittest.TestCase):
    def test_product_builtin_catalog_contains_only_shipped_servers(self) -> None:
        self.assertEqual(list_builtin_server_names(), ["deepcube", "doc_search"])

    def test_resolve_builtin_spawn_config_uses_current_interpreter(self) -> None:
        with (
            patch("deep_agent.capabilities.mcp.builtin_config.sys.executable", "/tmp/python"),
            patch("deep_agent.capabilities.mcp.builtin_config.MCPS_DIR", TEST_MCPS_DIR),
        ):
            resolved = resolve_builtin_spawn_config("echo_fixture", {"env": {"FOO": "bar"}})

        self.assertEqual(resolved["command"], "/tmp/python")
        self.assertEqual(
            resolved["args"],
            [str(TEST_MCPS_DIR / "echo_fixture" / "server.py")],
        )
        self.assertEqual(resolved["env"], {"FOO": "bar"})

    def test_normalize_mcp_config_builtin_transport(self) -> None:
        with patch("deep_agent.capabilities.mcp.builtin_config.MCPS_DIR", TEST_MCPS_DIR):
            transport, config = normalize_mcp_config(
                "echo_fixture",
                "builtin",
                {"env": {"FOO": "1"}},
            )
        self.assertEqual(transport, "builtin")
        self.assertEqual(config["command"], sys.executable)
        self.assertEqual(
            config["args"],
            [str(TEST_MCPS_DIR / "echo_fixture" / "server.py")],
        )
        self.assertEqual(config["env"], {"FOO": "1"})

    def test_normalize_mcp_config_rejects_stdio(self) -> None:
        with self.assertRaisesRegex(ValueError, "stdio MCP transport is no longer supported"):
            normalize_mcp_config(
                "external",
                "stdio",
                {"command": "python", "args": ["server.py"]},
            )


if __name__ == "__main__":
    unittest.main()
