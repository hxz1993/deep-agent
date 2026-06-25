from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from deep_agent.capabilities.mcp.builtin_config import (
    normalize_mcp_config,
    resolve_builtin_spawn_config,
)
from deep_agent.core.paths import DEEP_AGENT_ROOT


class BuiltinMcpConfigTests(unittest.TestCase):
    def test_resolve_builtin_spawn_config_uses_current_interpreter(self) -> None:
        with patch("deep_agent.capabilities.mcp.builtin_config.sys.executable", "/tmp/python"):
            resolved = resolve_builtin_spawn_config("text_tools", {"env": {"FOO": "bar"}})

        self.assertEqual(resolved["command"], "/tmp/python")
        self.assertEqual(
            resolved["args"],
            [str(DEEP_AGENT_ROOT / "mcps" / "text_tools" / "server.py")],
        )
        self.assertEqual(resolved["env"], {"FOO": "bar"})

    def test_normalize_mcp_config_builtin_transport(self) -> None:
        transport, config = normalize_mcp_config(
            "text_tools",
            "builtin",
            {"env": {"FOO": "1"}},
        )
        self.assertEqual(transport, "builtin")
        self.assertEqual(config["command"], sys.executable)
        self.assertEqual(
            config["args"],
            [str(DEEP_AGENT_ROOT / "mcps" / "text_tools" / "server.py")],
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
