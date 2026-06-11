"""MCP toolset 层集成测试；完整 HTTP 路径见 test_chat_header_forwarding.py。"""

from __future__ import annotations

import unittest

from tests.integration.helpers.mcp import (
    call_echo_tool,
    stdio_echo_toolset,
    streamable_http_echo_server,
    url_echo_toolset,
)


class McpHeaderForwardingTests(unittest.IsolatedAsyncioTestCase):
    async def test_stdio_mcp_server_receives_forwarded_headers(self) -> None:
        forwarded = {
            "Authorization": "Bearer stdio-integration-token",
            "X-Tenant-Id": "tenant-stdio",
        }
        received = await call_echo_tool(stdio_echo_toolset(), forwarded)
        self.assertEqual(
            received,
            {
                "authorization": "Bearer stdio-integration-token",
                "x-tenant-id": "tenant-stdio",
            },
        )

    async def test_http_mcp_server_receives_forwarded_headers(self) -> None:
        forwarded = {
            "X-User-Token": "http-user-token",
            "X-Tenant-Id": "tenant-http",
        }
        with streamable_http_echo_server() as mcp_url:
            received = await call_echo_tool(url_echo_toolset(mcp_url), forwarded)
        self.assertEqual(
            received,
            {
                "x-user-token": "http-user-token",
                "x-tenant-id": "tenant-http",
            },
        )

    async def test_http_mcp_merges_static_and_request_headers(self) -> None:
        forwarded = {"X-Tenant-Id": "tenant-merge"}
        static = {"X-Api-Key": "static-key"}
        with streamable_http_echo_server() as mcp_url:
            received = await call_echo_tool(
                url_echo_toolset(mcp_url, static_headers=static),
                forwarded,
            )
        self.assertEqual(received.get("x-api-key"), "static-key")
        self.assertEqual(received.get("x-tenant-id"), "tenant-merge")


if __name__ == "__main__":
    unittest.main()
