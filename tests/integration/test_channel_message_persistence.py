"""SDK ingress through the channel gateway with independently read durable history.

Uses a disposable file-backed SQLite, real HTTP/SSE/SDK and a fake WeCom wire.
No real bot, provider, shared database or fixed local service port is contacted.
"""
from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.messages import UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from sqlalchemy import select

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.db.models import ChannelMessageRun
from deep_agent.schemas.agent import AgentUpdate
from deep_agent.schemas.ui_tools import UiToolCreate, UiToolPackageCreate
from deep_agent.skills.runtime import SkillRuntimeLibrary
from tests.helpers.request_identity import install_test_user_header
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent
from tests.integration.helpers.chat import mock_request_disconnect
from tests.integration import test_wecom_ingress as ingress_fixture
from tests.unit.test_wecom_adapter import frame


class ChannelMessagePersistenceIntegrationTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the streaming ASGI->aiohttp bridge, not a buffered fake SSE source.
    _create_ingress = ingress_fixture.WecomIngressIntegrationTests._create_ingress

    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(
            prefix="TMP_to_delete-channel-message-facts-", dir=Path(__file__).resolve().parents[2],
        )
        self.addCleanup(temporary.cleanup)
        stores, engine, factory = await make_sqlite_stores(
            f"sqlite+aiosqlite:///{Path(temporary.name) / 'channel.sqlite3'}",
        )
        self._agent_id = await seed_default_agent(stores, name="isolated-channel-facts")
        await stores.agent.update_unique(self._agent_id, AgentUpdate(capabilities=["builtin:general"]))
        self.ctx = AppContext.for_testing(
            registry=build_registry(), stores=stores, db_engine=engine,
            db_session_factory=factory,
            skill_runtime=SkillRuntimeLibrary(Path(temporary.name) / "skills"),
        )
        init_context(self.ctx)
        # Registered before ingress resources, so teardown closes adapters and
        # gateway tasks before closing the store and removing the temporary DB.
        async def close_context():
            try:
                await self.ctx.aclose()
            finally:
                reset_context()
        self.addAsyncCleanup(close_context)
        await self.ctx.event_persister.start()
        self._app = FastAPI()
        install_test_user_header(self._app)
        self._app.include_router(api_router)
        self.http = AsyncClient(transport=ASGITransport(app=self._app), base_url="http://test")
        self.addAsyncCleanup(self.http.aclose)
        no_title = patch("deep_agent.agent.runner.AgentRunner._generate_model_title", new_callable=AsyncMock)
        no_title.start()
        self.addCleanup(no_title.stop)
        env = patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "fixture-token"})
        env.start()
        self.addCleanup(env.stop)

    async def _history(self, session_id):
        # Do not flush the event writer: history must already be committed at
        # the instant the external IM delivery is attempted.
        response = await self.http.get(f"/api/v1/sessions/{session_id}/history")
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def _run_record(self, account_id, message_id):
        async with self.ctx.db_session_factory() as db:
            return (await db.execute(select(ChannelMessageRun).where(
                ChannelMessageRun.account_id == account_id,
                ChannelMessageRun.external_message_id == message_id,
            ))).scalar_one()

    async def _drain(self, service):
        async with asyncio.timeout(10):
            while any(peer.active or peer.pending or peer.worker for peer in service.peers.values()):
                await asyncio.sleep(0.01)

    async def _send(self, handler, adapter, *, message_id, peer_type="single",
                    message="Synthetic read-only channel verification"):
        handler.handle_frame(frame(
            payload={"content": message},
            msgid=message_id, chattype=peer_type, chatid="isolated-synthetic-group",
        ), adapter.client)
        await adapter.client.flush()

    def _assert_items(self, history, *, texts, tool_count):
        items = history["items"]
        self.assertEqual(len([i for i in items if i["kind"] == "user_message"]), 1)
        self.assertEqual([i["text"] for i in items if i["kind"] == "assistant_message" and i["text"]], texts)
        tools = [i for i in items if i["kind"] == "tool"]
        self.assertEqual(len(tools), tool_count)
        self.assertEqual(len({i["item_id"] for i in items}), len(items))
        self.assertNotIn("<think>", str(items))
        return tools

    async def _two_rounds(self, *, peer_type="single", fail_delivery=None):
        account, service, adapter, handler, requests = await self._create_ingress(stream_interval=0)
        message_id = f"durable-two-rounds-{peer_type}-{fail_delivery}"
        ready, release = asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)
        model_calls = 0
        final_delivery_histories = []

        async def answer(messages, info):
            nonlocal model_calls
            model_calls += 1
            if model_calls <= 2:
                yield f"PROCESS_{model_calls}"
                tool = next(t for t in info.function_tools if t.name.endswith("get_current_time"))
                yield {1: DeltaToolCall(name=tool.name, json_args="{}", tool_call_id=f"clock-{model_calls}")}
            else:
                ready.set()
                await release.wait()
                yield "FINAL"

        async def deliver(_frame, _stream_id, text, finish):
            if finish:
                history = await self._history(requests[-1]["session_id"])
                self._assert_items(history, texts=["PROCESS_1", "PROCESS_2", "FINAL"], tool_count=2)
                final_delivery_histories.append(history)
                if fail_delivery == "final":
                    raise ConnectionError("synthetic final delivery failure")
            elif fail_delivery == "preview" and text != "<think></think>":
                raise ConnectionError("synthetic temporary delivery failure")

        adapter.client.reply_stream.side_effect = deliver
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(stream_function=answer)), mock_request_disconnect():
            await self._send(handler, adapter, message_id=message_id, peer_type=peer_type)
            try:
                await asyncio.wait_for(ready.wait(), 5)
                during = await self._history(requests[-1]["session_id"])
                tools = self._assert_items(during, texts=["PROCESS_1", "PROCESS_2"], tool_count=2)
                self.assertTrue(all(t["status"] == "completed" and t["output"] for t in tools))
                self.assertEqual(self.ctx.active_runs.active_count(), 1)
                if fail_delivery != "preview":
                    async with asyncio.timeout(3):
                        while adapter.client.reply_stream.await_args.args[2] != "PROCESS_1\n\nPROCESS_2\n\n处理中…":
                            await asyncio.sleep(0.01)
                    # Assert the currently visible waiting frame, not merely
                    # that accumulated text was sent sometime in the past.
                    self.assertEqual(adapter.client.reply_stream.await_args.args[2:], (
                        "PROCESS_1\n\nPROCESS_2\n\n处理中…", False,
                    ))
            finally:
                release.set()
                await self._drain(service)
            # Re-delivery of the same incoming message must not replay tools,
            # including when the final outgoing acknowledgement was lost.
            await self._send(handler, adapter, message_id=message_id, peer_type=peer_type)
            await self._drain(service)
        replies = [call.args for call in adapter.client.reply_stream.await_args_list]
        self.assertEqual(model_calls, 3)
        self.assertEqual(len(requests), 1)
        self.assertEqual(len({args[1] for args in replies}), 1)
        self.assertEqual(replies[-1][2:], ("FINAL", True))
        self.assertEqual(len(final_delivery_histories), 1)
        if fail_delivery != "preview":
            self.assertIn(("PROCESS_1\n\nPROCESS_2", False), [args[2:] for args in replies])
        record = await self._run_record(account.id, message_id)
        self.assertEqual(record.processing_status, "completed")
        self.assertEqual(record.delivery_status, "failed" if fail_delivery == "final" else "sent")
        self.assertEqual(record.error_code, "delivery_failed" if fail_delivery == "final" else None)
        self.assertEqual(self.ctx.active_runs.active_count(), 0)
        await self._assert_fact_events(requests[0]["session_id"], status="completed")

    async def _assert_fact_events(self, session_id, *, status):
        events = await self.ctx.stores.coordinator.list_all_events(session_id)
        self.assertFalse(any(row.event_type.startswith("run/") for row in events))
        failures = [row for row in events if row.event_type == "execution/error"]
        self.assertEqual(len(failures), 1 if status == "failed" else 0)

    async def test_private_two_tool_rounds_are_durable_before_final_delivery(self):
        await self._two_rounds()

    async def test_isolated_group_has_same_durability_and_final_replacement(self):
        await self._two_rounds(peer_type="group")

    async def test_failed_preview_does_not_replay_or_block_durable_final(self):
        await self._two_rounds(fail_delivery="preview")

    async def test_failed_final_delivery_preserves_events_and_is_not_reported_sent(self):
        await self._two_rounds(fail_delivery="final")

    async def _interrupted_after_tool(self, *, cancel):
        account, service, adapter, handler, requests = await self._create_ingress(stream_interval=0)
        ready, release, provider_closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        self.addCleanup(release.set)
        model_calls = 0
        message_id = "durable-cancel" if cancel else "durable-failure"

        async def answer(messages, info):
            nonlocal model_calls
            model_calls += 1
            if model_calls == 1:
                yield "PRESERVED_PROCESS"
                tool = next(t for t in info.function_tools if t.name.endswith("get_current_time"))
                yield {1: DeltaToolCall(name=tool.name, json_args="{}", tool_call_id="preserved-clock")}
            else:
                ready.set()
                try:
                    await release.wait()
                finally:
                    provider_closed.set()
                raise RuntimeError("synthetic later provider failure")

        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(stream_function=answer)), mock_request_disconnect():
            await self._send(handler, adapter, message_id=message_id)
            try:
                await asyncio.wait_for(ready.wait(), 5)
                self._assert_items(await self._history(requests[0]["session_id"]), texts=["PRESERVED_PROCESS"], tool_count=1)
                if cancel:
                    response = await self.http.post(f"/api/v1/sessions/{requests[0]['session_id']}/cancel")
                    self.assertEqual(response.status_code, 200, response.text)
                    self.assertEqual(response.json()["status"], "cancel_requested")
                else:
                    release.set()
                await self._drain(service)
            finally:
                release.set()
        self.assertTrue(provider_closed.is_set())
        self.assertEqual(model_calls, 2)
        replies = [call.args for call in adapter.client.reply_stream.await_args_list]
        self.assertTrue(replies[-1][3])
        self.assertIn("已停止" if cancel else "处理失败", replies[-1][2])
        self.assertNotIn("<think>", replies[-1][2])
        self.assertEqual(len({args[1] for args in replies}), 1)
        history = await self._history(requests[0]["session_id"])
        self._assert_items(history, texts=["PRESERVED_PROCESS"], tool_count=1)
        self.assertFalse(any(i["status"] in {"running", "pending", "awaiting_input"} for i in history["items"]))
        record = await self._run_record(account.id, message_id)
        self.assertEqual(record.processing_status, "interrupted" if cancel else "failed")
        self.assertEqual(record.delivery_status, "sent")
        self.assertEqual(self.ctx.active_runs.active_count(), 0)
        await self._assert_fact_events(requests[0]["session_id"], status="interrupted" if cancel else "failed")

    async def test_console_cancel_ends_channel_loading_and_preserves_completed_tool(self):
        await self._interrupted_after_tool(cancel=True)

    async def test_provider_failure_ends_channel_loading_and_preserves_completed_tool(self):
        await self._interrupted_after_tool(cancel=False)

    async def test_channel_hides_configured_ui_tools_and_continues_after_text_clarification(self):
        account, service, adapter, handler, requests = await self._create_ingress(stream_interval=0)
        await self.ctx.stores.ui_tool_package.create_manual(UiToolPackageCreate(code="ui:budget", label="预算"))
        await self.ctx.stores.ui_tool.create_manual(UiToolCreate(
            name="budget__clarify", package_code="ui:budget", description="Ask for the budget month.",
            parameters_json_schema={"type": "object"},
        ))
        prompt = "缺少预算月份时必须调用 UI 工具。"
        capabilities = ["ui:builtin", "ui:budget"]
        await self.ctx.stores.agent.update_unique(
            self._agent_id, AgentUpdate(prompt=prompt, capabilities=capabilities),
        )
        clarification = "请提供要核对的预算月份。"
        followup = "2026 年 11 月。"
        final_answer = "收到，按 2026 年 11 月核对。"
        delivered_histories = []

        async def answer(messages, info):
            tools = {tool.name for tool in (*info.function_tools, *info.output_tools)}
            self.assertTrue(tools.isdisjoint({"builtin__confirm", "builtin__choice", "budget__clarify"}))
            self.assertFalse(any(name.startswith("ui__") for name in tools))
            self.assertIn("channel_message", tools)
            self.assertIn(prompt, info.instructions)
            self.assertGreater(info.instructions.index("直接在当前渠道的回复正文"), info.instructions.index(prompt))
            user_parts = [p for m in messages for p in m.parts if isinstance(p, UserPromptPart)]
            if followup in str(user_parts[-1].content):
                self.assertTrue(any(clarification in str(message.parts) for message in messages))
                yield final_answer
            else:
                yield clarification

        async def deliver(_frame, _stream_id, _text, finish):
            if finish:
                delivered_histories.append(await self._history(requests[-1]["session_id"]))

        adapter.client.reply_stream.side_effect = deliver
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(stream_function=answer)), mock_request_disconnect():
            await self._send(handler, adapter, message_id="channel-ui-clarify", message="请核对预算。")
            await self._drain(service)
            self.assertEqual(adapter.client.reply_stream.await_args.args[2:], (clarification, True))
            self.assertEqual(len(delivered_histories), 1)
            self._assert_items(delivered_histories[0], texts=[clarification], tool_count=0)

            await self._send(handler, adapter, message_id="channel-ui-clarify-reply", message=followup)
            await self._drain(service)
            self.assertEqual(adapter.client.reply_stream.await_args.args[2:], (final_answer, True))

        self.assertEqual(len(requests), 2)
        self.assertEqual(requests[0]["session_id"], requests[1]["session_id"])
        self.assertEqual(len(delivered_histories), 2)
        items = delivered_histories[1]["items"]
        self.assertEqual([item["text"] for item in items if item["kind"] == "assistant_message" and item["text"]],
                         [clarification, final_answer])
        self.assertFalse(any(item["kind"] == "tool" or item["status"] in {"pending", "awaiting_input"} for item in items))
        for message_id in ("channel-ui-clarify", "channel-ui-clarify-reply"):
            record = await self._run_record(account.id, message_id)
            self.assertEqual((record.processing_status, record.delivery_status), ("completed", "sent"))
        row = await self.ctx.stores.agent.get_by_id(self._agent_id)
        self.assertEqual(row.capabilities, capabilities)
        self.assertEqual(self.ctx.active_runs.active_count(), 0)
        await self._assert_fact_events(requests[0]["session_id"], status="completed")
