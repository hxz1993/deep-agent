from __future__ import annotations

import unittest

from pydantic import ValidationError

from deep_agent.schemas.mcp import McpBuiltinConfig, McpServerCreate, McpServerUpdate


class McpSchemaBuiltinGuardTests(unittest.TestCase):
    def test_create_rejects_builtin_transport(self) -> None:
        with self.assertRaises(ValidationError):
            McpServerCreate.model_validate(
                {
                    "name": "text_tools",
                    "transport": "builtin",
                    "config": {},
                }
            )

    def test_create_accepts_stdio(self) -> None:
        row = McpServerCreate.model_validate(
            {
                "name": "external",
                "transport": "stdio",
                "config": {"command": "python", "args": ["server.py"]},
            }
        )
        self.assertEqual(row.transport, "stdio")

    def test_update_rejects_builtin_transport(self) -> None:
        with self.assertRaises(ValidationError):
            McpServerUpdate.model_validate(
                {
                    "transport": "builtin",
                    "config": {},
                }
            )

    def test_update_builtin_config_shape(self) -> None:
        cfg = McpBuiltinConfig.model_validate(
            {"env": {"DEEPCUBE_MCP_ENV_URL": "https://example.test/"}}
        )
        self.assertEqual(cfg.env, {"DEEPCUBE_MCP_ENV_URL": "https://example.test/"})


if __name__ == "__main__":
    unittest.main()
