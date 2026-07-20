from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from pydantic_ai.models.test import TestModel

from tests.helpers.request_identity import TEST_USER_ID, install_test_user_header
from tests.integration.helpers.chat import async_echo_chat_app, read_chat_stream, test_chat_client


class ChatHeaderForwardingStreamTest(unittest.IsolatedAsyncioTestCase):
    async def test_chat_stream_forwards_request_headers_to_mcp_tool(self) -> None:
        test_model = TestModel(
            call_tools=["echo_echo_request_headers"],
            custom_output_text="ok",
        )
        async with async_echo_chat_app() as (app, record_file):
            with patch("deep_agent.agent.factory._resolve_model", return_value=test_model):
                await read_chat_stream(
                    app,
                    json_body={
                        "message": "inspect headers",
                        "capabilities": ["mcp:echo"],
                    },
                    headers={
                        "Authorization": "Bearer stream-token",
                        "X-Tenant-Id": "tenant-stream",
                    },
                )

        recorded = json.loads(record_file.read_text(encoding="utf-8"))
        self.assertEqual(
            recorded,
            {
                "authorization": "Bearer stream-token",
                "user": TEST_USER_ID,
                "x-tenant-id": "tenant-stream",
            },
        )


class ChatHeaderForwardingTests(unittest.TestCase):
    """HTTP → AgentRunner → MCP 全链路；见 integration/README.md。"""

    def test_chat_api_forwards_request_headers_to_mcp_tool(self) -> None:
        with test_chat_client() as (client, record_file):
            response = client.post(
                "/api/v1/chat",
                json={
                    "message": "inspect headers",
                    "capabilities": ["mcp:echo"],
                },
                headers={
                    "Authorization": "Bearer chat-api-token",
                    "X-Tenant-Id": "tenant-chat",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["output"], "ok")
        recorded = json.loads(record_file.read_text(encoding="utf-8"))
        self.assertEqual(
            recorded,
            {
                "authorization": "Bearer chat-api-token",
                "user": TEST_USER_ID,
                "x-tenant-id": "tenant-chat",
            },
        )

    def test_chat_api_forwards_authenticated_user_without_other_headers(self) -> None:
        with test_chat_client() as (client, record_file):
            response = client.post(
                "/api/v1/chat",
                json={
                    "message": "inspect headers",
                    "capabilities": ["mcp:echo"],
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        recorded = json.loads(record_file.read_text(encoding="utf-8"))
        self.assertEqual(recorded, {"user": TEST_USER_ID})

    def test_chat_api_isolated_mcp_keeps_static_identity(self) -> None:
        with test_chat_client(
            request_context_mode="isolated",
            static_headers={"User": "static-mcp-user", "X-MCP-Token": "service-token"},
        ) as (client, record_file):
            response = client.post(
                "/api/v1/chat",
                json={
                    "message": "inspect headers",
                    "capabilities": ["mcp:echo"],
                },
                headers={
                    "Authorization": "Bearer request-token",
                    "X-Tenant-Id": "tenant-request",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        recorded = json.loads(record_file.read_text(encoding="utf-8"))
        self.assertEqual(
            recorded,
            {"user": "static-mcp-user", "x-mcp-token": "service-token"},
        )

    def test_chat_uses_production_builtin_loader_for_text_tools(self) -> None:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from deep_agent.api.v1 import api_router
        from deep_agent.capabilities import build_registry
        from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
        from deep_agent.core.context import AppContext, init_context, reset_context
        from tests.integration.helpers.chat import testing_data_stores

        manager = McpLeaseManager()
        stores, engine, session_factory = testing_data_stores(mcp_rows=[])
        init_context(
            AppContext.for_testing(
                registry=build_registry(),
                stores=stores,
                mcp_lease_manager=manager,
                db_engine=engine,
                db_session_factory=session_factory,
            )
        )

        app = FastAPI()
        install_test_user_header(app)
        app.include_router(api_router)

        test_model = TestModel(
            call_tools=["text_tools_reverse"],
            custom_output_text="ok",
        )
        try:
            with patch("deep_agent.agent.factory._resolve_model", return_value=test_model):
                with TestClient(app) as client:
                    response = client.post(
                        "/api/v1/chat",
                        json={
                            "message": "reverse abc",
                            "capabilities": ["mcp:text_tools"],
                        },
                        headers={
                            "Authorization": "Bearer text-tools-token",
                            "X-Tenant-Id": "tenant-text-tools",
                        },
                    )
        finally:
            reset_context()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["output"], "ok")


if __name__ == "__main__":
    unittest.main()
