from __future__ import annotations

import unittest

from pydantic import ValidationError

from deep_agent.schemas.mcp import (
    McpBuiltinConfig,
    McpServerCreate,
    McpServerRenameRequest,
    McpServerUpdate,
)


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

    def test_create_rejects_stdio(self) -> None:
        with self.assertRaises(ValidationError):
            McpServerCreate.model_validate(
                {
                    "name": "external",
                    "transport": "stdio",
                    "config": {"url": "http://127.0.0.1:8765/mcp"},
                }
            )

    def test_create_accepts_url(self) -> None:
        row = McpServerCreate.model_validate(
            {
                "name": "external",
                "transport": "url",
                "config": {"url": "http://127.0.0.1:8765/mcp"},
            }
        )
        self.assertEqual(row.transport, "url")
        self.assertEqual(row.config["request_context"], {"mode": "isolated"})

    def test_create_rejects_names_that_cannot_be_used_as_capability_ids(self) -> None:
        for name in ("1external", "external server", "external/server", "外部服务"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                McpServerCreate.model_validate(
                    {
                        "name": name,
                        "transport": "url",
                        "config": {"url": "http://127.0.0.1:8765/mcp"},
                    }
                )

    def test_rename_reuses_create_name_rules_and_trims_value(self) -> None:
        body = McpServerRenameRequest.model_validate({"new_name": "  renamed_mcp  "})
        self.assertEqual(body.new_name, "renamed_mcp")

        for name in ("1renamed", "renamed server", "renamed/server", "重命名"):
            with self.subTest(name=name), self.assertRaises(ValidationError):
                McpServerRenameRequest.model_validate({"new_name": name})

    def test_create_rejects_blank_or_non_http_url(self) -> None:
        for url in ("", "   ", "ftp://example.test/mcp", "not-a-url"):
            with self.subTest(url=url), self.assertRaises(ValidationError):
                McpServerCreate.model_validate(
                    {
                        "name": "external",
                        "transport": "url",
                        "config": {"url": url},
                    }
                )

    def test_create_rejects_non_positive_init_timeout(self) -> None:
        with self.assertRaises(ValidationError):
            McpServerCreate.model_validate(
                {
                    "name": "external",
                    "transport": "url",
                    "config": {
                        "url": "http://127.0.0.1:8765/mcp",
                        "init_timeout": 0,
                    },
                }
            )

    def test_create_preserves_explicit_inherit_mode(self) -> None:
        row = McpServerCreate.model_validate(
            {
                "name": "external",
                "transport": "url",
                "config": {
                    "url": "http://127.0.0.1:8765/mcp",
                    "request_context": {"mode": "inherit"},
                },
            }
        )
        self.assertEqual(row.config["request_context"], {"mode": "inherit"})

    def test_create_rejects_unknown_request_context_mode(self) -> None:
        with self.assertRaises(ValidationError):
            McpServerCreate.model_validate(
                {
                    "name": "external",
                    "transport": "url",
                    "config": {
                        "url": "http://127.0.0.1:8765/mcp",
                        "request_context": {"mode": "custom"},
                    },
                }
            )

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

    def test_update_allows_config_without_transport_for_builtin_patch(self) -> None:
        row = McpServerUpdate.model_validate(
            {
                "config": {
                    "env": {
                        "DEEPCUBE_MCP_ENV_URL": "https://example.test/",
                        "DEEPCUBE_MCP_DISCOVERY": '{"enabled":false}',
                    }
                },
                "enabled": True,
            }
        )
        self.assertIsNone(row.transport)
        self.assertIn("env", row.config)

    def test_update_rejects_null_required_fields(self) -> None:
        for field in ("transport", "config", "enabled"):
            with self.subTest(field=field), self.assertRaises(ValidationError):
                McpServerUpdate.model_validate({field: None})


if __name__ == "__main__":
    unittest.main()
