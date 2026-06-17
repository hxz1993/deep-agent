from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from deep_agent.capabilities.mcp.builtin_config import (
    is_legacy_builtin_stdio_config,
    normalize_mcp_config,
    resolve_builtin_stdio_config,
)
from deep_agent.core.paths import DEEP_AGENT_ROOT


class BuiltinMcpConfigTests(unittest.TestCase):
    def test_resolve_builtin_stdio_config_uses_current_interpreter(self) -> None:
        with patch("deep_agent.capabilities.mcp.builtin_config.sys.executable", "/tmp/python"):
            resolved = resolve_builtin_stdio_config("text_tools", {"env": {"FOO": "bar"}})

        self.assertEqual(resolved["command"], "/tmp/python")
        self.assertEqual(
            resolved["args"],
            [str(DEEP_AGENT_ROOT / "mcps" / "text_tools" / "server.py")],
        )
        self.assertEqual(resolved["env"], {"FOO": "bar"})

    def test_is_legacy_builtin_stdio_config_detects_repo_server_path(self) -> None:
        legacy = {
            "command": "/old/python",
            "args": ["/old/path/mcps/text_tools/server.py"],
        }
        self.assertTrue(is_legacy_builtin_stdio_config("text_tools", legacy))

    def test_is_legacy_builtin_stdio_config_rejects_external_stdio(self) -> None:
        external = {
            "command": "/usr/bin/node",
            "args": ["/opt/mcp/server.js"],
        }
        self.assertFalse(is_legacy_builtin_stdio_config("text_tools", external))

    def test_normalize_mcp_config_builtin_transport(self) -> None:
        transport, config = normalize_mcp_config(
            "text_tools",
            "builtin",
            {"env": {"FOO": "1"}},
        )
        self.assertEqual(transport, "stdio")
        self.assertEqual(config["command"], sys.executable)
        self.assertEqual(
            config["args"],
            [str(DEEP_AGENT_ROOT / "mcps" / "text_tools" / "server.py")],
        )
        self.assertEqual(config["env"], {"FOO": "1"})

    def test_normalize_mcp_config_rewrites_legacy_builtin_stdio(self) -> None:
        transport, config = normalize_mcp_config(
            "text_tools",
            "stdio",
            {
                "command": "/old/python",
                "args": ["/old/path/mcps/text_tools/server.py"],
                "env": {"FOO": "1"},
            },
        )
        self.assertEqual(transport, "stdio")
        self.assertEqual(config["command"], sys.executable)
        self.assertEqual(
            config["args"],
            [str(DEEP_AGENT_ROOT / "mcps" / "text_tools" / "server.py")],
        )
        self.assertEqual(config["env"], {"FOO": "1"})


if __name__ == "__main__":
    unittest.main()
