"""MCP toolset 层集成测试；完整 HTTP 路径见 test_chat_header_forwarding.py。"""

from __future__ import annotations

import unittest

from deep_agent.capabilities.mcp.session_pool import McpSessionPool, reset_mcp_session_pool, set_mcp_session_pool
from tests.integration.helpers.mcp import (
    call_echo_tool,
    call_startup_headers_tool,
    stdio_echo_toolset,
    streamable_http_echo_server,
    url_echo_toolset,
)


class McpHeaderForwardingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        reset_mcp_session_pool()
        set_mcp_session_pool(McpSessionPool())

    def tearDown(self) -> None:
        reset_mcp_session_pool()

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

    async def test_stdio_mcp_subprocess_receives_startup_headers(self) -> None:
        forwarded = {
            "Authorization": "Bearer startup-token",
            "X-Tenant-Id": "tenant-startup",
        }
        received = await call_startup_headers_tool(stdio_echo_toolset(), forwarded)
        self.assertEqual(
            received,
            {
                "authorization": "Bearer startup-token",
                "x-tenant-id": "tenant-startup",
            },
        )

    async def test_stdio_mcp_startup_headers_use_first_request_in_session(self) -> None:
        toolset = stdio_echo_toolset()
        first = {
            "Authorization": "Bearer first-token",
            "X-Tenant-Id": "tenant-first",
        }
        second = {
            "Authorization": "Bearer second-token",
            "X-Tenant-Id": "tenant-second",
        }

        await call_startup_headers_tool(toolset, first)
        received = await call_startup_headers_tool(toolset, second)
        self.assertEqual(
            received,
            {
                "authorization": "Bearer first-token",
                "x-tenant-id": "tenant-first",
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
