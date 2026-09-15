from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from pydantic_ai.models.test import TestModel

from tests.helpers.request_identity import TEST_USER_ID, install_test_user_header
from tests.integration.helpers.chat import (
    async_echo_chat_app,
    read_chat_stream,
    test_chat_client as _test_chat_client,
)


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
                "language": "zh-cn",
                "user": TEST_USER_ID,
                "x-tenant-id": "tenant-stream",
            },
        )

    async def test_channel_chat_and_stream_use_current_account_scope_for_mcp(self):
        import os
        from httpx import ASGITransport, AsyncClient
        from deep_agent.core.context import get_context
        from deep_agent.schemas.channel import ChannelAccountCreate, ChannelAccountUpdate

        model = TestModel(call_tools=["echo_echo_request_headers"], custom_output_text="ok")
        async with async_echo_chat_app() as (app, record_file):
            store = get_context().stores.channel
            account = await store.create_account(ChannelAccountCreate(
                name="scope test", bot_id="scope-fixture", secret="fixture",
                agent_id=app.state.test_agent_id, session_user_id="channel-owner", enabled=True,
                settings={"deepfos": {"enabled": True, "app_id": "first001"}},
            ))
            headers = {"user": "forged-owner", "app": "invalid", "space": "invalid",
                       "authorization": "Bearer fixture-platform", "cookie": "fixture-platform=1",
                       "language": "en",
                       "x-deep-agent-app": "forged001", "x-deep-agent-space": "forged",
                       "x-deep-agent-channel-account": account.id,
                       "x-deep-agent-channel-token": "fixture-internal"}
            body = {"agent_id": app.state.test_agent_id, "message": "inspect headers", "capabilities": ["mcp:echo"]}
            with patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "fixture-internal"}), patch(
                "deep_agent.agent.factory._resolve_model", return_value=model
            ):
                for app_id in ("first001", "second002", None):
                    await store.update_account(account.id, ChannelAccountUpdate(settings={
                        "deepfos": {"enabled": app_id is not None, "app_id": app_id},
                    }))
                    expected = {"language": "en", "user": "channel-owner",
                                "authorization": "Bearer fixture-platform", "cookie": "fixture-platform=1"}
                    if app_id:
                        expected.update(app=app_id, space=app_id[:-3])
                    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                        response = await client.post("/api/v1/chat", json=body, headers=headers)
                        self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(json.loads(record_file.read_text()), expected)
                    await read_chat_stream(app, json_body=body, headers=headers)
                    self.assertEqual(json.loads(record_file.read_text()), expected)


class ChatHeaderForwardingTests(unittest.TestCase):
    """HTTP → AgentRunner → MCP 全链路；见 integration/README.md。"""

    def test_chat_api_forwards_request_headers_to_mcp_tool(self) -> None:
        with _test_chat_client() as (client, record_file):
            response = client.post(
                "/api/v1/chat",
                json={
                    "agent_id": client.app.state.test_agent_id,
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
                "language": "zh-cn",
                "user": TEST_USER_ID,
                "x-tenant-id": "tenant-chat",
            },
        )

    def test_chat_api_forwards_authenticated_user_without_other_headers(self) -> None:
        with _test_chat_client() as (client, record_file):
            response = client.post(
                "/api/v1/chat",
                json={
                    "agent_id": client.app.state.test_agent_id,
                    "message": "inspect headers",
                    "capabilities": ["mcp:echo"],
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        recorded = json.loads(record_file.read_text(encoding="utf-8"))
        self.assertEqual(recorded, {"language": "zh-cn", "user": TEST_USER_ID})

    def test_chat_api_derives_context_from_app_header(self) -> None:
        with _test_chat_client() as (client, record_file):
            response = client.post(
                "/api/v1/chat",
                json={
                    "agent_id": client.app.state.test_agent_id,
                    "message": "inspect headers",
                    "capabilities": ["mcp:echo"],
                },
                headers={"app": "mdxath003"},
            )

        self.assertEqual(response.status_code, 200, response.text)
        recorded = json.loads(record_file.read_text(encoding="utf-8"))
        self.assertEqual(
            recorded,
            {
                "app": "mdxath003",
                "language": "zh-cn",
                "space": "mdxath",
                "user": TEST_USER_ID,
            },
        )

    def test_chat_api_translates_console_context_header_for_mcp(self) -> None:
        with _test_chat_client() as (client, record_file):
            response = client.post(
                "/api/v1/chat",
                json={
                    "agent_id": client.app.state.test_agent_id,
                    "message": "inspect headers",
                    "capabilities": ["mcp:echo"],
                },
                headers={
                    "x-deep-agent-app": "iirjgi005",
                    "x-deep-agent-language": "zh-cn",
                },
            )

        self.assertEqual(response.status_code, 200, response.text)
        recorded = json.loads(record_file.read_text(encoding="utf-8"))
        self.assertEqual(
            recorded,
            {
                "app": "iirjgi005",
                "language": "zh-cn",
                "space": "iirjgi",
                "user": TEST_USER_ID,
            },
        )

    def test_chat_api_isolated_mcp_keeps_static_identity(self) -> None:
        with _test_chat_client(
            request_context_mode="isolated",
            static_headers={"User": "static-mcp-user", "X-MCP-Token": "service-token"},
        ) as (client, record_file):
            response = client.post(
                "/api/v1/chat",
                json={
                    "agent_id": client.app.state.test_agent_id,
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

    def test_chat_uses_test_builtin_loader_for_echo_fixture(self) -> None:
        import asyncio

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from deep_agent.api.v1 import api_router
        from deep_agent.capabilities import build_registry
        from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
        from deep_agent.core.context import AppContext, init_context, reset_context
        from tests.integration.helpers.chat import testing_data_stores
        from tests.helpers.builtin_mcp_fixture import use_test_builtin_mcp
        from tests.helpers.sqlite_stores import seed_default_agent

        manager = McpLeaseManager()
        stores, engine, session_factory = testing_data_stores(mcp_rows=[])
        agent_id = asyncio.run(
            seed_default_agent(stores, name="builtin-echo-agent")
        )
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
            call_tools=["echo_fixture_reverse"],
            custom_output_text="ok",
        )
        try:
            with use_test_builtin_mcp(), patch(
                "deep_agent.agent.factory._resolve_model", return_value=test_model
            ):
                with TestClient(app) as client:
                    response = client.post(
                        "/api/v1/chat",
                        json={
                            "agent_id": agent_id,
                            "message": "reverse abc",
                            "capabilities": ["mcp:echo_fixture"],
                        },
                        headers={
                            "Authorization": "Bearer echo-fixture-token",
                            "X-Tenant-Id": "tenant-echo-fixture",
                        },
                    )
        finally:
            reset_context()

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["output"], "ok")


if __name__ == "__main__":
    unittest.main()
