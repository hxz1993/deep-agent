"""MCP toolset 层集成测试；完整 HTTP 路径见 test_chat_header_forwarding.py。"""

from __future__ import annotations

import unittest

from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import reset_context
from tests.helpers.testing_context import init_test_context
from tests.integration.helpers.mcp import (
    call_echo_tool,
    streamable_http_echo_server,
    url_echo_toolset,
)


class McpHeaderForwardingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        init_test_context(mcp_lease_manager=McpLeaseManager())

    def tearDown(self) -> None:
        reset_context()

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
