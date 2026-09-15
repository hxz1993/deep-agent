from __future__ import annotations

import unittest

from deep_agent.mcp_request_context import (
    resolve_mcp_headers,
    resolve_mcp_request_context_mode,
)


class McpRequestContextPolicyTests(unittest.TestCase):
    def test_inherited_space_key_excludes_login_headers_and_internal_user(self):
        config = {"headers": {"Authorization": "static-auth", "Cookie": "static-cookie",
                              "User": "static-owner", "X-Deepfos-Key": "static-key",
                              "x-mcp-token": "separate-service"}}
        request = {"user": "session-owner", "app": "fixture007", "space": "fixture",
                   "language": "zh-cn", "x-deepfos-key": "fixture-space-key"}
        self.assertEqual(resolve_mcp_headers("url", config, request), {
            "app": "fixture007", "space": "fixture", "language": "zh-cn",
            "x-deepfos-key": "fixture-space-key", "x-mcp-token": "separate-service",
        })
        self.assertEqual(request["user"], "session-owner")
        config["request_context"] = {"mode": "isolated"}
        self.assertEqual(resolve_mcp_headers("url", config, request), config["headers"])

    def test_legacy_url_rows_continue_to_inherit(self) -> None:
        config = {"url": "http://example.test/mcp", "headers": {"user": "static"}}
        self.assertEqual(resolve_mcp_request_context_mode("url", config), "inherit")
        self.assertEqual(
            resolve_mcp_headers("url", config, {"user": "request", "space": "s1"}),
            {"user": "request", "space": "s1"},
        )

    def test_isolated_url_uses_static_headers_only(self) -> None:
        config = {
            "url": "http://example.test/mcp",
            "headers": {"user": "static", "x-mcp-token": "service"},
            "request_context": {"mode": "isolated"},
        }
        self.assertEqual(
            resolve_mcp_headers("url", config, {"user": "request", "space": "s1"}),
            {"user": "static", "x-mcp-token": "service"},
        )

    def test_inherit_merges_static_and_request_with_request_winning(self) -> None:
        config = {
            "url": "http://example.test/mcp",
            "headers": {"user": "static", "x-mcp-token": "service"},
            "request_context": {"mode": "inherit"},
        }
        self.assertEqual(
            resolve_mcp_headers("url", config, {"user": "request", "space": "s1"}),
            {"user": "request", "space": "s1", "x-mcp-token": "service"},
        )

    def test_builtin_is_always_inherit(self) -> None:
        config = {
            "headers": {"user": "static"},
            "request_context": {"mode": "isolated"},
        }
        self.assertEqual(resolve_mcp_request_context_mode("builtin", config), "inherit")
        self.assertEqual(
            resolve_mcp_headers("builtin", config, {"user": "request"}),
            {"user": "request"},
        )


if __name__ == "__main__":
    unittest.main()
