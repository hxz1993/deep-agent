from __future__ import annotations

import json
import unittest

from tests.integration.helpers.chat import test_chat_client


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
                "x-tenant-id": "tenant-chat",
            },
        )

    def test_chat_api_without_request_headers_leaves_mcp_meta_empty(self) -> None:
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
        self.assertEqual(recorded, {})

    def test_chat_stream_forwards_request_headers_to_mcp_tool(self) -> None:
        with test_chat_client() as (client, record_file):
            with client.stream(
                "POST",
                "/api/v1/chat/stream",
                json={
                    "message": "inspect headers",
                    "capabilities": ["mcp:echo"],
                },
                headers={
                    "Authorization": "Bearer stream-token",
                    "X-Tenant-Id": "tenant-stream",
                },
            ) as response:
                self.assertEqual(response.status_code, 200)
                list(response.iter_lines())

        recorded = json.loads(record_file.read_text(encoding="utf-8"))
        self.assertEqual(
            recorded,
            {
                "authorization": "Bearer stream-token",
                "x-tenant-id": "tenant-stream",
            },
        )

    def test_chat_uses_production_builtin_loader_for_text_tools(self) -> None:
        from unittest.mock import patch

        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from pydantic_ai.models.test import TestModel

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
