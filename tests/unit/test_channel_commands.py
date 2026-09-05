from __future__ import annotations

import asyncio
import unittest
from dataclasses import replace
from uuid import uuid4
from unittest.mock import AsyncMock, patch

from sqlalchemy import func, select

from deep_agent.channel_gateway.deepagent_client import StreamEvent
from deep_agent.channels.commands import parse_command, command_catalog, help_text
from deep_agent.channels.contracts import InboundMessage, InboundMedia
from deep_agent.channels.service import ChannelService, MessageJob
from deep_agent.channels.adapters.wecom.messages import parse_message
from deep_agent.db.models import ChatSession
from tests.unit.test_channel_gateway import ChannelGatewayTests
from tests.unit.test_wecom_adapter import frame


class FakeAdapter:
    def __init__(self):
        self.replies = []
        self.fail_delivery = False

    def new_reply_id(self):
        return uuid4().hex

    async def reply_loading(self, message, reply_id):
        await self.reply(message, reply_id, "正在处理…", False)

    async def reply(self, message, reply_id, content, finish):
        if self.fail_delivery and finish:
            raise ConnectionError("fixture delivery failure")
        self.replies.append((message.message_id, content, finish))


class FakeAgent:
    def __init__(self):
        self.calls = []
        self.running = {}
        self.started = asyncio.Event()
        self.allow_cancel = True
        self.hold = False

    async def stream_chat(self, account, *, session_id, message, attachments, context_refs=None):
        self.calls.append((session_id, message))
        done = asyncio.Event()
        self.running[session_id] = done
        self.started.set()
        yield StreamEvent("session", {"run_id": uuid4().hex})
        yield StreamEvent("thinking", {"delta": "private trace"})
        yield StreamEvent("text", {"delta": "回答"})
        if self.hold:
            await done.wait()
            yield StreamEvent("cancelled", {"status": "cancelled"})
        else:
            yield StreamEvent("final", {"status": "completed"})
        self.running.pop(session_id, None)

    async def cancel_session(self, account, session_id, **kwargs):
        done = self.running.get(session_id)
        if done and self.allow_cancel:
            done.set()
        return {"status": "cancel_requested" if done else "not_running"}

    async def run_status(self, account, session_id, **kwargs):
        return {"status": "running" if session_id in self.running else "idle"}


class ChannelCommandTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = ChannelGatewayTests.asyncSetUp
    asyncTearDown = ChannelGatewayTests.asyncTearDown

    def message(self, text, *, identifier=None, peer="u", peer_type="single", sender="sender"):
        metadata = {"chat_id": peer} if peer_type == "group" else {"user_id": sender}
        return InboundMessage("text", identifier or uuid4().hex, "req", sender, peer_type, peer, metadata, text, payload_hash="a" * 64)

    def service(self):
        adapter, agent = FakeAdapter(), FakeAgent()
        service = ChannelService(self.store, agent, stop_timeout=0.3)
        self.addAsyncCleanup(service.close_account, self.account.id)
        return service, adapter, agent

    async def count_sessions(self):
        async with self.factory() as db:
            return await db.scalar(select(func.count()).select_from(ChatSession))

    async def wait_idle(self, service):
        async with asyncio.timeout(3):
            while any(peer.active or peer.pending or peer.controls or peer.blocked for peer in service.peers.values()):
                await asyncio.sleep(0.01)

    def test_parser_only_accepts_standalone_channel_text(self):
        for command in ("/help", "/new", "/stop", "/status", "/whoami"):
            self.assertEqual(parse_command(self.message(command)), command)
        for text in ("/reset", "/new model", "/unknown"):
            self.assertEqual(parse_command(self.message(text)), "unknown")
        for text in ("请解释 /new", "/new\n正文", "> /stop", "/tmp/a.txt"):
            self.assertIsNone(parse_command(self.message(text)))
        self.assertIsNone(parse_command(replace(self.message("/stop"), command_eligible=False)))
        self.assertIsNone(parse_command(replace(self.message("/new"), media=(InboundMedia("file", "x", "text/plain", object()),))))
        self.assertEqual(len(command_catalog()["commands"]), 5)
        self.assertNotIn("/reset", help_text())

    async def test_read_commands_do_not_create_session_or_agent_run(self):
        service, adapter, agent = self.service()
        for command in ("/help", "/status", "/whoami", "/stop", "/reset"):
            await service.receive(self.account, adapter, self.message(command))
        self.assertEqual(await self.count_sessions(), 0)
        self.assertEqual(agent.calls, [])
        self.assertEqual(len(adapter.replies), 5)

    async def test_plain_group_commands_are_intercepted_before_agent_and_deduplicated(self):
        service, adapter, agent = self.service()
        for command in ("/help", "/whoami", "/status", "/stop", "/unknown"):
            message = parse_message(frame(payload={"content": command},
                                          msgid=command, chattype="group", chatid="group-test"))
            await service.receive(self.account, adapter, message)
        self.assertEqual(agent.calls, [])
        self.assertEqual(await self.count_sessions(), 0)
        self.assertEqual(len(adapter.replies), 5)
        message = parse_message(frame(payload={"content": "/new"},
                                      msgid="new-once", chattype="group", chatid="group-test"))
        await service.receive(self.account, adapter, message)
        await service.receive(self.account, adapter, message)
        self.assertEqual(await self.count_sessions(), 1)
        self.assertEqual(agent.calls, [])

    async def test_group_senders_are_per_message_context_not_binding_identity(self):
        service, adapter, agent = self.service()
        inputs = []

        async def stream(account, **kwargs):
            inputs.append(kwargs)
            yield StreamEvent("final", {"status": "completed", "output": "回答"})

        agent.stream_chat = stream
        for sender in ("member-a", "member-b"):
            await service.receive(self.account, adapter, self.message("我的问题", peer="shared-group", peer_type="group", sender=sender, identifier=sender))
        await self.wait_idle(service)
        self.assertEqual(len(inputs), 2)
        self.assertEqual(inputs[0]["session_id"], inputs[1]["session_id"])
        for request, sender in zip(inputs, ("member-a", "member-b")):
            source = request["context_refs"][-1]
            self.assertEqual(source["type"], "channel.message_source")
            self.assertEqual(source["payload"]["sender_id"], sender)
            self.assertEqual(source["payload"]["peer_id"], "shared-group")
            run = await self.store.get_message(self.account.id, sender)
            self.assertEqual(run.sender_id, sender)
        binding = await self.store.get_binding(self.account.id, "group", "shared-group")
        self.assertEqual(binding.peer_metadata, {"chat_id": "shared-group"})

    async def test_quote_reaches_chat_as_context_with_uploaded_media_not_raw_protocol(self):
        service, adapter, agent = self.service()
        adapter.download_media = AsyncMock(return_value=(b"fixture-bytes", "quote.jpg"))
        agent.upload_media = AsyncMock(return_value={"id": "fixture-image", "name": "quote.jpg", "mime": "image/jpeg"})
        inputs = []

        async def stream(account, **kwargs):
            from deep_agent.schemas.chat import StreamChatRequest
            inputs.append(StreamChatRequest(agent_id=account.agent_id, **kwargs))
            yield StreamEvent("final", {"status": "completed", "output": "已看到引用"})

        agent.stream_chat = stream
        message = parse_message(frame(payload={"content": "@测试机器人 解释引用内容"},
            chattype="group", chatid="group-test", quote={"msgtype": "mixed", "mixed": {"msg_item": [
                {"msgtype": "text", "text": {"content": "引用原文 /stop"}},
                {"msgtype": "image", "image": {"url": "https://media.invalid/quote", "aeskey": "private-fixture-key"}},
            ]}}))
        await service.receive(self.account, adapter, message)
        await self.wait_idle(service)
        self.assertEqual(len(inputs), 1)
        request = inputs[0]
        self.assertEqual(request.message, "@测试机器人 解释引用内容")
        self.assertEqual(request.context_refs[0].payload, {
            "message_type": "mixed", "text": "引用原文 /stop",
            "attachments": [{"id": "fixture-image", "name": "quote.jpg", "mime": "image/jpeg"}],
        })
        self.assertEqual(request.attachments[0].id, "fixture-image")
        adapter.download_media.assert_awaited_once_with(message.quote.media[0])
        self.assertNotIn("private-fixture-key", request.model_dump_json())
        self.assertNotIn("https://media.invalid", request.model_dump_json())
        run = await self.store.get_message(self.account.id, message.message_id)
        self.assertEqual((run.processing_status, run.delivery_status), ("completed", "sent"))

    async def test_failed_quote_download_does_not_silently_run_without_reference(self):
        service, adapter, agent = self.service()
        adapter.download_media = AsyncMock(side_effect=RuntimeError("media unavailable"))
        message = parse_message(frame(quote={"msgtype": "image", "image": {
            "url": "https://media.invalid/quote", "aeskey": "fixture-key",
        }}))
        await service.receive(self.account, adapter, message)
        await self.wait_idle(service)
        self.assertEqual(agent.calls, [])
        self.assertEqual((await self.store.get_message(self.account.id, message.message_id)).processing_status, "failed")
        self.assertIn("失败", adapter.replies[-1][1])
        self.assertTrue(adapter.replies[-1][2])

    async def test_stop_during_media_preflight_finishes_the_original_loading_bubble(self):
        service, adapter, agent = self.service()
        downloading = asyncio.Event()

        async def download(_media):
            downloading.set()
            await asyncio.Event().wait()

        adapter.download_media = download
        message = replace(self.message("查看图片", identifier="loading-media"),
                          media=(InboundMedia("image", "x.jpg", "image/jpeg", object()),))
        await service.receive(self.account, adapter, message)
        await asyncio.wait_for(downloading.wait(), 2)
        await service.receive(self.account, adapter, self.message("/stop"))
        await self.wait_idle(service)
        original = [(text, finish) for identifier, text, finish in adapter.replies
                    if identifier == "loading-media"]
        self.assertEqual(original, [("正在处理…", False), ("已停止。", True)])
        self.assertEqual(agent.calls, [])
        row = await self.store.get_message(self.account.id, "loading-media")
        self.assertEqual((row.processing_status, row.delivery_status), ("interrupted", "sent"))

    async def test_gateway_shutdown_finishes_bubble_and_preserves_partial_text(self):
        service, adapter, agent = self.service()
        agent.hold = True
        service.stream_interval = 0
        await service.receive(self.account, adapter, self.message("等待", identifier="shutdown-loading"))
        await asyncio.wait_for(agent.started.wait(), 2)
        async with asyncio.timeout(2):
            while adapter.replies[-1][1] != "回答":
                await asyncio.sleep(0.01)
        self.assertEqual(adapter.replies[-1], ("shutdown-loading", "回答", False))
        await service.close_account(self.account.id)
        self.assertEqual(adapter.replies[-1], ("shutdown-loading", "回答\n\n服务已停止，请稍后重试。", True))
        row = await self.store.get_message(self.account.id, "shutdown-loading")
        self.assertEqual((row.processing_status, row.delivery_status), ("interrupted", "sent"))

    async def test_new_is_atomic_and_duplicate_does_not_rotate_after_delivery_failure(self):
        service, adapter, agent = self.service()
        message = self.message("/new", identifier="new-once")
        adapter.fail_delivery = True
        await service.receive(self.account, adapter, message)
        first = await self.store.get_binding(self.account.id, "single", "u")
        await service.receive(self.account, adapter, message)
        second = await self.store.get_binding(self.account.id, "single", "u")
        self.assertEqual(first.session_id, second.session_id)
        self.assertEqual(await self.count_sessions(), 1)
        run = await self.store.get_message(self.account.id, "new-once")
        self.assertEqual((run.processing_status, run.delivery_status), ("completed", "failed"))
        self.assertEqual(agent.calls, [])

    async def test_stop_bypasses_active_worker_and_discards_old_queue(self):
        service, adapter, agent = self.service()
        agent.hold = True
        await service.receive(self.account, adapter, self.message("long", identifier="long"))
        await asyncio.wait_for(agent.started.wait(), 2)
        await service.receive(self.account, adapter, self.message("queued", identifier="queued"))
        await service.receive(self.account, adapter, self.message("/stop"))
        await self.wait_idle(service)
        self.assertEqual(len(agent.calls), 1)
        self.assertEqual((await self.store.get_message(self.account.id, "long")).processing_status, "interrupted")
        self.assertEqual((await self.store.get_message(self.account.id, "queued")).error_code, "user_stopped")
        self.assertEqual(await self.count_sessions(), 1)

    async def test_busy_new_preserves_old_history_pointer_and_uses_new_session(self):
        service, adapter, agent = self.service()
        agent.hold = True
        await service.receive(self.account, adapter, self.message("long"))
        await asyncio.wait_for(agent.started.wait(), 2)
        old_id = agent.calls[0][0]
        await service.receive(self.account, adapter, self.message("/new"))
        agent.hold = False
        await service.receive(self.account, adapter, self.message("after"))
        await self.wait_idle(service)
        self.assertNotEqual(old_id, agent.calls[-1][0])
        self.assertEqual(await self.count_sessions(), 2)
        self.assertIsNotNone(await self.store.get_session_origin(old_id))

    async def test_arrivals_during_new_wait_for_new_session(self):
        service, adapter, agent = self.service()
        agent.hold = True
        await service.receive(self.account, adapter, self.message("long"))
        await asyncio.wait_for(agent.started.wait(), 2)
        entered, release = asyncio.Event(), asyncio.Event()
        cancel = agent.cancel_session

        async def wait_cancel(*args, **kwargs):
            entered.set()
            await release.wait()
            return await cancel(*args, **kwargs)

        agent.cancel_session = wait_cancel
        control = asyncio.create_task(service.receive(self.account, adapter, self.message("/new")))
        await asyncio.wait_for(entered.wait(), 2)
        await service.receive(self.account, adapter, self.message("arrived during switch"))
        self.assertEqual(len(agent.calls), 1)
        agent.hold = False
        release.set()
        await control
        await self.wait_idle(service)
        self.assertEqual(len(agent.calls), 2)
        self.assertNotEqual(agent.calls[0][0], agent.calls[1][0])

    async def test_shutdown_cancels_inflight_new_before_rotation(self):
        service, adapter, _agent = self.service()
        entered = asyncio.Event()

        async def waiting(*_args):
            entered.set()
            await asyncio.Event().wait()

        with patch.object(service, "_wait_stopped", side_effect=waiting):
            control = asyncio.create_task(service.receive(self.account, adapter, self.message("/new")))
            await asyncio.wait_for(entered.wait(), 2)
            await service.close_account(self.account.id)
            self.assertTrue(control.cancelled())
        self.assertEqual(await self.count_sessions(), 0)
        self.assertEqual(service.peers, {})

    async def test_cancelled_preflight_task_is_not_retryable(self):
        service, adapter, _agent = self.service()
        with patch.object(service, "_kick"):
            await service.receive(self.account, adapter, self.message("before dispatch", identifier="preflight"))
            peer = next(iter(service.peers.values()))
            job = peer.pending.popleft()
            peer.active = job
            job.task = asyncio.create_task(asyncio.sleep(30))
            await service.receive(self.account, adapter, self.message("/stop"))
            peer.active = None
        run = await self.store.get_message(self.account.id, "preflight")
        self.assertEqual((run.processing_status, run.error_code), ("interrupted", "user_stopped"))
        self.assertFalse(self.store.can_retry(run))

    async def test_deleted_session_requires_positive_completion_evidence(self):
        service, adapter, agent = self.service()
        job = MessageJob(self.account, adapter, self.message("x"), "run", "reply", 1,
                         session_id="deleted", dispatched=True)
        agent.cancel_session = AsyncMock(side_effect=RuntimeError("404"))
        with patch.object(self.store, "get_channel_session", new=AsyncMock(return_value=None)):
            with self.assertRaises(RuntimeError):
                await service._wait_stopped(self.account, "deleted", job, 0.1)
            job.terminal = True
            self.assertTrue(await service._wait_stopped(self.account, "deleted", job, 0.1))

    async def test_stop_timeout_does_not_rotate_and_resumes_only_when_confirmed(self):
        service, adapter, agent = self.service()
        service.stop_timeout = 0.03
        agent.hold = True
        agent.allow_cancel = False
        await service.receive(self.account, adapter, self.message("long"))
        await asyncio.wait_for(agent.started.wait(), 2)
        old_id = agent.calls[0][0]
        await service.receive(self.account, adapter, self.message("/new", identifier="timeout"))
        self.assertEqual(await self.count_sessions(), 1)
        self.assertEqual((await self.store.get_message(self.account.id, "timeout")).error_code, "stop_timeout")
        await service.receive(self.account, adapter, self.message("after"))
        self.assertEqual(len(agent.calls), 1)
        agent.hold = False
        agent.allow_cancel = True
        await self.wait_idle(service)
        self.assertEqual(agent.calls[-1][0], old_id)

    async def test_different_users_isolated_same_group_shared_and_no_hidden_trace(self):
        service, adapter, agent = self.service()
        for peer, peer_type, sender in (("a", "single", "a"), ("b", "single", "b"), ("g", "group", "a"), ("g", "group", "b")):
            await service.receive(self.account, adapter, self.message("hi", peer=peer, peer_type=peer_type, sender=sender))
            await self.wait_idle(service)
        self.assertEqual(await self.count_sessions(), 3)
        self.assertEqual(agent.calls[-1][0], agent.calls[-2][0])
        self.assertNotIn("private trace", str(adapter.replies))
