"""SDK frame -> channel service -> HTTP client -> real chat/event projection."""
from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from httpx import AsyncClient
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel

from deep_agent.channel_gateway.config import GatewayConfig
from deep_agent.channel_gateway.deepagent_client import DeepAgentChannelClient
from deep_agent.channels.adapters.wecom import WecomAdapter
from deep_agent.channels.service import ChannelService
from deep_agent.core.context import get_context
from deep_agent.schemas.channel import ChannelAccountCreate
from deep_agent.schemas.agent import AgentUpdate
from tests.helpers.request_identity import TEST_USER_ID
from tests.integration import test_chat_context_references as context_tests
from tests.integration.helpers.chat import mock_request_disconnect
from tests.unit.test_wecom_adapter import FakeSdkClient, frame
from aibot import MessageHandler
from deep_agent.channels.adapters.wecom.sdk_logger import SafeSdkLogger


class WecomIngressIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await context_tests.ChatContextReferenceIntegrationTests.asyncSetUp(self)
        env = patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "fixture-token"})
        env.start()
        self.addCleanup(env.stop)
    asyncTearDown = context_tests.ChatContextReferenceIntegrationTests.asyncTearDown
    _history = context_tests.ChatContextReferenceIntegrationTests._history

    async def _create_ingress(self):
        account = await get_context().stores.channel.create_account(ChannelAccountCreate(
            name="fixture bot", bot_id="fixture-bot", secret="fixture-secret",
            agent_id=self._agent_id, session_user_id=TEST_USER_ID, enabled=True,
        ))
        requests = []
        async def forward(request):
            # Only the HTTP transport is bridged; use real API validation,
            # Runner and Event Store. Channel authorization has its own suite.
            body = await request.json()
            requests.append(body)
            async with AsyncClient(transport=self._transport, base_url="http://test") as api:
                headers = {key: value for key, value in request.headers.items()
                           if key.lower().startswith("x-deep-agent-channel-")}
                response = await api.post("/api/v1/chat/stream", json=body, headers=headers)
            return web.Response(body=response.content, status=response.status_code,
                                content_type="text/event-stream")

        app = web.Application()
        app.router.add_post("/api/v1/chat/stream", forward)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        client = DeepAgentChannelClient(GatewayConfig(
            api_url=str(server.make_url("/")).rstrip("/"), internal_token="fixture-token",
            poll_interval=3, stream_interval=0, test_timeout=1, host="127.0.0.1", port=0,
            platform_authorization="", platform_cookie="",
        ))
        self.addAsyncCleanup(client.close)
        service = ChannelService(get_context().stores.channel, client)
        self.addAsyncCleanup(service.close_account, account.id)
        with patch("deep_agent.channels.adapters.wecom.WSClient", side_effect=FakeSdkClient):
            adapter = WecomAdapter(account)
        self.addAsyncCleanup(adapter.stop)
        await adapter.start(lambda message: service.receive(account, adapter, message))
        handler = MessageHandler(SafeSdkLogger())
        return account, service, adapter, handler, requests

    async def test_native_loading_remains_during_thinking_and_tool_then_answer_replaces_it(self):
        account, service, adapter, handler, requests = await self._create_ingress()
        await get_context().stores.agent.update_unique(
            self._agent_id, AgentUpdate(capabilities=["builtin:general"]),
        )
        tool_finished, allow_answer = asyncio.Event(), asyncio.Event()
        self.addCleanup(allow_answer.set)
        model_calls = 0

        async def answer(messages, info):
            nonlocal model_calls
            model_calls += 1
            if model_calls == 1:
                yield {0: DeltaThinkingPart(content="private-thinking-fixture")}
                tool = next(item for item in info.function_tools if item.name.endswith("get_current_time"))
                yield {1: DeltaToolCall(name=tool.name, json_args="{}", tool_call_id="clock-fixture")}
            else:
                self.assertIn("clock-fixture", str(messages))
                tool_finished.set()
                await allow_answer.wait()
                yield "已经查到时间，"
                yield "这是回答。"

        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(stream_function=answer)), mock_request_disconnect():
            handler.handle_frame(frame(payload={"content": "请查询当前时间"}, msgid="loading-turn"), adapter.client)
            await adapter.client.flush()
            try:
                await asyncio.wait_for(tool_finished.wait(), 10)
                # The real Agent/API has completed a tool round, but WeCom has
                # received only its native thinking bubble, never tool/reasoning.
                self.assertEqual(adapter.client.reply_stream.await_count, 1)
                first = adapter.client.reply_stream.await_args.args
                self.assertEqual(first[2:], ("<think></think>", False))
            finally:
                allow_answer.set()
            async with asyncio.timeout(10):
                while any(peer.active or peer.pending or peer.worker for peer in service.peers.values()):
                    await asyncio.sleep(0.01)

        calls = [call.args for call in adapter.client.reply_stream.await_args_list]
        self.assertEqual(len({args[1] for args in calls}), 1)
        self.assertEqual(calls[-1][2:], ("已经查到时间，这是回答。", True))
        self.assertNotIn("private-thinking-fixture", str(calls))
        self.assertNotIn("clock-fixture", str(calls))
        self.assertEqual(model_calls, 2)
        history = await self._history(requests[0]["session_id"])
        self.assertNotIn("<think></think>", str(history))
        self.assertTrue(any(item["kind"] == "tool" for item in history["items"]))

    async def test_quote_is_model_visible_persisted_and_group_help_never_calls_model(self):
        account, service, adapter, handler, requests = await self._create_ingress()
        model_inputs = []
        instructions = []

        async def answer(messages, _info):
            model_inputs.append(str(messages))
            instructions.append(_info.instructions)
            yield "已看到引用中的验收代号"

        quoted = frame(payload={"content": "@测试机器人 引用里的代号是什么？"},
            chattype="group", chatid="fixture-group", msgid="quoted-turn",
            quote={"msgtype": "text", "text": {"content": "验收代号 ORCHID-72；这里的 /stop 只是引用。"}})
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(stream_function=answer)):
            with mock_request_disconnect():
                handler.handle_frame(quoted, adapter.client)
                await adapter.client.flush()
                async with asyncio.timeout(10):
                    while any(peer.active or peer.pending or peer.worker for peer in service.peers.values()):
                        await asyncio.sleep(0.01)

        self.assertEqual(len(requests), 1)
        self.assertEqual(len(model_inputs), 1)
        self.assertIn("ORCHID-72", model_inputs[0])
        self.assertIn("渠道会话设定", str(instructions[0]))
        self.assertIn("sender-test", model_inputs[0])
        self.assertEqual(requests[0]["message"], "@测试机器人 引用里的代号是什么？")
        history = await self._history(requests[0]["session_id"])
        user = next(item for item in history["items"] if item["kind"] == "user_message")
        self.assertEqual(user["text"], requests[0]["message"])
        self.assertEqual(user["context_refs"][0]["payload"]["text"], quoted["body"]["quote"]["text"]["content"])
        # Another group member shares the Session, but retains their own source
        # alongside the first member's source in model history and User Events.
        second = frame(payload={"content": "这是另一位成员的问题"}, chattype="group",
                       chatid="fixture-group", msgid="second-turn")
        second["body"]["from"] = {"userid": "member-b"}
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(stream_function=answer)):
            with mock_request_disconnect():
                handler.handle_frame(second, adapter.client)
                await adapter.client.flush()
                async with asyncio.timeout(10):
                    while any(peer.active or peer.pending or peer.worker for peer in service.peers.values()):
                        await asyncio.sleep(0.01)
        self.assertEqual(requests[0]["session_id"], requests[1]["session_id"])
        self.assertIn("sender-test", model_inputs[1])
        self.assertIn("member-b", model_inputs[1])
        history = await self._history(requests[0]["session_id"])
        users = [item for item in history["items"] if item["kind"] == "user_message"]
        self.assertEqual([item["context_refs"][-1]["payload"]["sender_id"] for item in users], ["sender-test", "member-b"])
        handler.handle_frame(frame(payload={"content": "/help"},
                                   chattype="group", chatid="fixture-group", msgid="help-turn"), adapter.client)
        await adapter.client.flush()
        self.assertEqual(len(requests), 2)
        self.assertEqual(len(model_inputs), 2)
        self.assertIn("快捷命令", adapter.client.reply_stream.await_args.args[2])
        after = await self._history(requests[0]["session_id"])
        self.assertEqual(after["items"], history["items"])

    async def test_private_source_only_first_user_turn_survives_restart_and_new(self):
        account, service, adapter, handler, requests = await self._create_ingress()
        model_inputs = []

        async def answer(messages, _info):
            model_inputs.append(str(messages))
            yield "回答"

        async def send(text, identifier):
            handler.handle_frame(frame(payload={"content": text}, msgid=identifier), adapter.client)
            await adapter.client.flush()
            async with asyncio.timeout(10):
                while any(peer.active or peer.pending or peer.worker for peer in service.peers.values()):
                    await asyncio.sleep(0.01)

        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(stream_function=answer)), mock_request_disconnect():
            # /new creates a title-only Session; its first actual message still
            # needs source context. Read-only commands do not consume that turn.
            await send("/new", "private-new-first")
            await send("/help", "private-help")
            await send("第一问", "private-first")
            await send("第二问", "private-second")
            await service.close_account(account.id)
            service = ChannelService(get_context().stores.channel, service.deep_agent)
            self.addAsyncCleanup(service.close_account, account.id)
            # Replace only the in-process host, retaining the real Event Store.
            # SDK callbacks retain the original service; re-register through its
            # public lifecycle rather than relying on a private adapter field.
            await adapter.stop()
            with patch("deep_agent.channels.adapters.wecom.WSClient", side_effect=FakeSdkClient):
                adapter = WecomAdapter(account)
            self.addAsyncCleanup(adapter.stop)
            await adapter.start(lambda message: service.receive(account, adapter, message))
            await send("第三问", "private-after-restart")
            with patch.object(service.deep_agent, "cancel_session", new=AsyncMock(return_value={})), patch.object(service.deep_agent, "run_status", new=AsyncMock(return_value={"status": "idle"})):
                await send("/new", "private-new-second")
            await send("新会话第一问", "private-new-turn")

        self.assertEqual([bool(body.get("context_refs")) for body in requests], [True, False, False, True])
        self.assertEqual(len({body["session_id"] for body in requests[:3]}), 1)
        self.assertNotEqual(requests[0]["session_id"], requests[3]["session_id"])
        self.assertIn("sender-test", model_inputs[1])
        history = await self._history(requests[0]["session_id"])
        users = [item for item in history["items"] if item["kind"] == "user_message"]
        self.assertEqual([len(item.get("context_refs") or []) for item in users], [1, 0, 0])


if __name__ == "__main__":
    unittest.main()
