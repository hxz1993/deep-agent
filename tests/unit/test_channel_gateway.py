from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, event, insert, select

from deep_agent.channels.gateway.app import create_gateway_app
from deep_agent.channels.gateway.config import GatewayConfig
from deep_agent.channels.gateway.deepagent_client import DeepAgentChannelClient, StreamEvent
from deep_agent.channels.adapters.wecom.messages import parse_message
from deep_agent.channels.gateway.runtime import AccountRuntime, ChannelRuntime
from deep_agent.channels.service import ChannelService
from deep_agent.db.dialect import DbDialect
from deep_agent.db.factory import create_engine
from deep_agent.db.models import Agent, Base, ChannelMessageRun, ChatSession, LlmModel, LlmProvider
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.schemas.channel import ChannelAccountCreate, ChannelAccountUpdate


class FakeWeComClient:
    def __init__(self) -> None:
        self.replies: list[tuple[str, bool]] = []

    async def reply_stream(self, _frame, _stream_id, content, finish=False):
        self.replies.append((content, finish))

    def new_reply_id(self):
        return "reply-id"

    async def reply_loading(self, message, reply_id, content=""):
        await self.reply(message, reply_id, f"{content}\n\n正在处理…" if content else "正在处理…", False)

    async def reply(self, message, reply_id, content, finish):
        await self.reply_stream(None, reply_id, content, finish)


class FakeDeepAgentClient:
    async def upload_media(self, *_args, **_kwargs):
        raise AssertionError("text test must not upload media")

    async def stream_chat(self, *_args, **_kwargs):
        yield StreamEvent("session", {"run_id": "run-1"})
        yield StreamEvent("thinking", {"delta": "hidden"})
        yield StreamEvent("tool_call", {"tool": "hidden_tool"})
        yield StreamEvent("text", {"delta": "你"})
        yield StreamEvent("text", {"delta": "好"})
        yield StreamEvent("final", {"status": "completed"})


class PresentationDeepAgentClient(FakeDeepAgentClient):
    def __init__(self, events):
        self.events = events
        self.calls = 0
        self.closed = False

    async def stream_chat(self, *_args, **_kwargs):
        self.calls += 1
        try:
            yield StreamEvent("session", {"run_id": "presentation-run"})
            for item in self.events:
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            self.closed = True


class PresentationWeComClient(FakeWeComClient):
    def __init__(self, *, fail_loading=False, fail_preview=False, fail_wait=False, fail_final=False):
        super().__init__()
        self.fail_loading = fail_loading
        self.fail_preview = fail_preview
        self.fail_wait = fail_wait
        self.fail_final = fail_final
        self.attempts = []

    async def reply_stream(self, _frame, stream_id, content, finish=False):
        self.attempts.append((stream_id, content, finish))
        if (
            (finish and self.fail_final)
            or (not finish and content == "正在处理…" and self.fail_loading)
            or (not finish and content != "正在处理…" and self.fail_preview)
            or (not finish and content.endswith("\n\n正在处理…") and self.fail_wait)
        ):
            raise ConnectionError("fixture reply transport unavailable")
        await super().reply_stream(_frame, stream_id, content, finish)


class StructuredOutputChannelAdapter(PresentationWeComClient):
    def __init__(self, *, text_limit=20480, fail_native=None):
        super().__init__()
        self.text_limit = text_limit
        self.fail_native = fail_native
        self.native_attempts = []
        self.native_delivered = []

    def message_capabilities(self):
        return SimpleNamespace(text_limit=self.text_limit)

    async def send_component(self, target, component, *, reply_to_current=False):
        from deep_agent.channels.outbound import ComponentReceipt
        request_id = f"native-request-{len(self.native_attempts) + 1}"
        self.native_attempts.append((request_id, target, component.text))
        if len(self.native_attempts) == self.fail_native:
            raise TimeoutError("synthetic uncertain send acknowledgment")
        self.native_delivered.append((request_id, target, component.text))
        return ComponentReceipt(kind="text", request_id=request_id)


class ChannelGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        path = Path(self.tmp.name) / "channel.db"
        self.engine, self.factory = create_engine(f"sqlite+aiosqlite:///{path}")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with self.factory.begin() as db:
            await db.execute(
                insert(LlmProvider.__table__),
                {
                    "id": "1" * 32,
                    "name": "provider",
                    "provider_type": "openai",
                    "use_default_base_url": True,
                    "base_url": "https://example.test/v1",
                },
            )
            await db.execute(
                insert(LlmModel.__table__),
                {
                    "id": "00000000-0000-0000-0000-000000000001",
                    "provider_id": "1" * 32,
                    "name": "model",
                    "model": "openai:test",
                    "enabled": True,
                },
            )
            for number in (1, 2):
                await db.execute(
                    insert(Agent.__table__),
                    {
                        "id": f"00000000-0000-0000-0000-00000000000{number}",
                        "name": f"agent-{number}",
                        "model_id": "00000000-0000-0000-0000-000000000001",
                        "capabilities": [],
                        "skills": [],
                        "prompt": "test",
                    },
                )
        self.stores = create_data_stores(DbDialect.SQLITE, self.factory)
        assert self.stores.channel is not None
        self.store = self.stores.channel
        self.account = await self.store.create_account(
            ChannelAccountCreate(
                name="企微测试",
                bot_id="bot",
                secret="secret",
                agent_id="00000000-0000-0000-0000-000000000001",
                session_user_id="owner",
                enabled=True,
            ),
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()
        self.tmp.cleanup()

    async def _run_presentation(self, events, *, message_id="presentation", adapter=None, stream_interval=0, sent_texts=()):
        message = parse_message({
            "headers": {"req_id": f"request-{message_id}"},
            "body": {
                "msgtype": "text", "msgid": message_id,
                "from": {"userid": "presentation-user"},
                "text": {"content": "本地展示测试，不执行业务修改"},
            },
        })
        agent = PresentationDeepAgentClient(events)
        adapter = adapter or PresentationWeComClient()
        service = ChannelService(self.store, agent, stream_interval=stream_interval)
        if sent_texts:
            original_stream = agent.stream_chat
            async def confirmed_send_stream(*args, **kwargs):
                async for event in original_stream(*args, **kwargs):
                    if event.event == "final":
                        job = service.peers[(self.account.id, message.peer_type, message.peer_id)].active
                        job.message_tool_sent = True
                        job.sent_text_hashes.update(hashlib.sha256(text.strip().encode()).hexdigest() for text in sent_texts)
                    yield event
            agent.stream_chat = confirmed_send_stream
        await service.receive(self.account, adapter, message)
        peer = service.peers[(self.account.id, message.peer_type, message.peer_id)]
        if peer.worker is not None:
            await asyncio.wait_for(peer.worker, timeout=3)
        row = await self.store.get_message(self.account.id, message_id)
        self.assertTrue(agent.closed)
        self.assertTrue(all(stream_id == "reply-id" for stream_id, _, _ in adapter.attempts))
        return service, agent, adapter, row, message

    async def test_structured_output_utf8_chunks_preserve_long_chinese_emoji_tail(self):
        value = {"text": "合同😀中文" * 4000 + "最后一项🧾", "flag": False, "count": 0, "empty": None}
        adapter = StructuredOutputChannelAdapter()
        service, agent, adapter, row, message = await self._run_presentation([
            StreamEvent("output", {"accepted": False, "output": {"private": "rejected candidate"}}),
            StreamEvent("final", {"status": "completed", "output": value,
                                  "output_info": {"name": "lease_result", "output_id": "accepted"}}),
        ], message_id="structured-long", adapter=adapter)
        first = [text for text, finish in adapter.replies if finish]
        self.assertEqual(len(first), 1)
        parts = first + [text for _, _, text in adapter.native_delivered]
        self.assertGreater(len(parts), 2)
        self.assertTrue(all(len(text.encode("utf-8")) <= adapter.text_limit for text in parts))
        self.assertTrue(all(text.startswith("lease_result [") for text in parts))
        raw = "".join(text.split("```json\n", 1)[1].removesuffix("\n```") for text in parts)
        self.assertEqual(json.loads(raw), value)
        self.assertIn("最后一项🧾", raw)
        self.assertEqual(row.processing_status, "completed")
        self.assertEqual(row.delivery_status, "sent")
        self.assertTrue(all(target.account_id == self.account.id and target.peer_id == message.peer_id
                            and target.message_id == message.message_id for _, target, _ in adapter.native_delivered))
        self.assertEqual(len({request_id for request_id, _, _ in adapter.native_delivered}), len(parts) - 1)
        self.assertEqual(agent.calls, 1)

    async def test_structured_output_failed_second_segment_stops_without_retry_or_first_duplication(self):
        adapter = StructuredOutputChannelAdapter(text_limit=256, fail_native=1)
        service, agent, adapter, row, message = await self._run_presentation([
            StreamEvent("final", {"status": "completed", "output": {"text": "长中文😀" * 1000},
                                  "output_info": {"name": "lease_result"}}),
        ], message_id="structured-send-failure", adapter=adapter)
        self.assertEqual(len([text for text, finish in adapter.replies if finish]), 1)
        self.assertEqual(len(adapter.native_attempts), 1)
        self.assertEqual(adapter.native_delivered, [])
        self.assertEqual(row.processing_status, "completed")
        self.assertEqual(row.delivery_status, "failed")
        attempts = list(adapter.attempts), list(adapter.native_attempts)
        await service.receive(self.account, adapter, message)
        self.assertEqual((adapter.attempts, adapter.native_attempts), attempts)
        self.assertEqual(agent.calls, 1)

    async def test_structured_output_scalar_false_zero_null_empty_is_visible_json(self):
        for index, value in enumerate((False, 0, None, {}, [], "", [False, 0, None])):
            with self.subTest(value=value):
                _, _, adapter, row, _ = await self._run_presentation([
                    StreamEvent("final", {"status": "completed", "output": value,
                                          "output_info": {"name": "typed_result"}}),
                ], message_id=f"structured-scalar-{index}", adapter=StructuredOutputChannelAdapter())
                text, finish = adapter.replies[-1]
                self.assertTrue(finish)
                self.assertTrue(text.startswith("typed_result\n```json\n"))
                decoded = json.loads(text.split("```json\n", 1)[1].removesuffix("\n```"))
                self.assertEqual(decoded, value)
                self.assertIs(type(decoded), type(value))
                self.assertEqual(row.delivery_status, "sent")

    async def test_structured_output_confirmed_proactive_text_is_not_sent_again(self):
        from deep_agent.channels.service import structured_output_text_parts
        value = {"ok": True, "text": "已由主动工具发出"}
        raw, parts = structured_output_text_parts("lease_result", value, 20480)
        for index, sent in enumerate((raw, parts[0])):
            with self.subTest(sent=index):
                _, _, adapter, row, _ = await self._run_presentation([
                    StreamEvent("final", {"status": "completed", "output": value,
                                          "output_info": {"name": "lease_result"}}),
                ], message_id=f"structured-dedup-{index}", adapter=StructuredOutputChannelAdapter(), sent_texts=[sent])
                self.assertEqual(adapter.replies[-1], ("已发送。", True))
                self.assertEqual(adapter.native_attempts, [])
                self.assertEqual(row.delivery_status, "sent")

    async def test_structured_output_pending_error_candidates_are_never_delivered(self):
        for index, status in enumerate(("pending", "failed", "interrupted")):
            with self.subTest(status=status):
                _, _, adapter, row, _ = await self._run_presentation([
                    StreamEvent("output", {"accepted": False, "output": {"private": "PRIVATE_CANDIDATE"}}),
                    StreamEvent("final", {"status": status, "output": "PRIVATE_CANDIDATE",
                                          "output_info": {"name": "lease_result"}}),
                ], message_id=f"structured-non-success-{index}", adapter=StructuredOutputChannelAdapter())
                self.assertNotIn("PRIVATE_CANDIDATE", adapter.replies[-1][0])
                self.assertEqual(adapter.native_attempts, [])

    async def test_presentation_accumulates_phases_with_newlines_and_only_rearms_loading_on_transition(self):
        _, _, adapter, row, _ = await self._run_presentation([
            StreamEvent("thinking", {"delta": "PRIVATE_THINKING"}),
            StreamEvent("thinking", {"delta": "PRIVATE_THINKING_2"}),
            StreamEvent("tool_call", {"tool": "PRIVATE_TOOL", "args": {"secret": "PRIVATE_ARGS"}}),
            StreamEvent("text", {"delta": "第一轮："}),
            StreamEvent("text", {"delta": "准备读取。"}),
            StreamEvent("thinking", {"delta": "PRIVATE_NEXT_THOUGHT"}),
            StreamEvent("thinking", {"delta": "PRIVATE_REPEATED_THOUGHT"}),
            StreamEvent("tool_call", {"tool": "PRIVATE_TOOL_2"}),
            StreamEvent("tool_result", {"result": "PRIVATE_RESULT"}),
            StreamEvent("tool_call_removed", {"reason": "PRIVATE_REMOVED"}),
            StreamEvent("text", {"delta": "第二轮预览"}),
            StreamEvent("context_compaction_progress", {"message": "PRIVATE_COMPACTION"}),
            StreamEvent("context_compaction_progress", {"message": "PRIVATE_COMPACTION_2"}),
            StreamEvent("text", {"delta": "第三轮临时正文，比最终答案长很多。"}),
            StreamEvent("final", {"status": "completed", "output": "完成"}),
        ])
        self.assertEqual(adapter.replies, [
            ("正在处理…", False),
            ("第一轮：", False),
            ("第一轮：准备读取。", False),
            ("第一轮：准备读取。\n\n正在处理…", False),
            ("第一轮：准备读取。\n\n第二轮预览", False),
            ("第一轮：准备读取。\n\n第二轮预览\n\n正在处理…", False),
            ("第一轮：准备读取。\n\n第二轮预览\n\n第三轮临时正文，比最终答案长很多。", False),
            ("完成", True),
        ])
        self.assertNotIn("PRIVATE_", "".join(text for text, _ in adapter.replies))
        self.assertEqual((row.processing_status, row.delivery_status), ("completed", "sent"))

    async def test_presentation_native_search_interleaving_does_not_keep_old_tail(self):
        _, _, adapter, _, _ = await self._run_presentation([
            StreamEvent("text", {"delta": "Searching with the provider's native tool."}),
            StreamEvent("tool_call", {
                "tool": "web_search", "source": "web", "execution": "native",
                "tool_call_id": "native-search", "args": {"query": "PRIVATE_QUERY"},
            }),
            StreamEvent("tool_result", {
                "tool": "web_search", "source": "web", "execution": "native",
                "tool_call_id": "native-search", "result": "PRIVATE_RESULT",
            }),
            StreamEvent("text", {"delta": "已经找到答案，正在整理。"}),
            StreamEvent("final", {"status": "completed", "output": "最终答案"}),
        ])
        self.assertEqual(adapter.replies, [
            ("正在处理…", False),
            ("Searching with the provider's native tool.", False),
            ("Searching with the provider's native tool.\n\n正在处理…", False),
            ("Searching with the provider's native tool.\n\n已经找到答案，正在整理。", False),
            ("最终答案", True),
        ])

    async def test_presentation_explicit_empty_or_invalid_final_never_restores_preview(self):
        for number, output in enumerate(("", " \n\t", None, {"unexpected": "shape"})):
            with self.subTest(output=output):
                _, _, adapter, row, _ = await self._run_presentation([
                    StreamEvent("text", {"delta": "不能作为最终答案的过程正文"}),
                    StreamEvent("final", {"status": "completed", "output": output}),
                ], message_id=f"empty-final-{number}")
                self.assertEqual(adapter.replies[-1], ("已处理完成，但没有可展示的文本结果。", True))
                self.assertEqual((row.processing_status, row.delivery_status), ("completed", "sent"))

    async def test_loading_preserves_throttled_tail_even_without_another_text_phase(self):
        with patch("deep_agent.channels.service.time", SimpleNamespace(monotonic=lambda: 100.0)):
            _, _, adapter, _, _ = await self._run_presentation([
                StreamEvent("text", {"delta": "已读取"}),
                StreamEvent("text", {"delta": "两份资料，正在对比。"}),
                StreamEvent("tool_call", {"tool": "some_tool"}),
                StreamEvent("thinking", {"delta": "PRIVATE_REASONING"}),
                StreamEvent("tool_call", {"tool": "another_tool"}),
                StreamEvent("final", {"status": "completed", "output": "完成"}),
            ], stream_interval=1)
        self.assertEqual(adapter.replies, [
            ("正在处理…", False),
            ("已读取", False),
            ("已读取两份资料，正在对比。\n\n正在处理…", False),
            ("完成", True),
        ])

    async def test_presentation_legacy_missing_output_uses_only_last_text_phase(self):
        _, _, adapter, _, _ = await self._run_presentation([
            StreamEvent("text", {"delta": "旧的过程正文"}),
            StreamEvent("tool_call", {"tool": "some_tool"}),
            StreamEvent("text", {"delta": "最后"}),
            StreamEvent("text", {"delta": "一段"}),
            StreamEvent("final", {"status": "completed"}),
        ])
        self.assertEqual(adapter.replies[-1], ("最后一段", True))

    async def test_presentation_legacy_missing_output_after_tool_does_not_restore_prior_phase(self):
        _, _, adapter, _, _ = await self._run_presentation([
            StreamEvent("text", {"delta": "工具之前的临时内容"}),
            StreamEvent("tool_call", {"tool": "some_tool"}),
            StreamEvent("tool_result", {"result": "PRIVATE_RESULT"}),
            StreamEvent("final", {"status": "completed"}),
        ])
        self.assertEqual(adapter.replies[-1], ("已处理完成，但没有可展示的文本结果。", True))

    async def test_presentation_final_ignores_late_text_thinking_and_other_terminal_events(self):
        _, _, adapter, row, _ = await self._run_presentation([
            StreamEvent("text", {"delta": "临时预览"}),
            StreamEvent("final", {"status": "completed", "output": "已完成"}),
            StreamEvent("text", {"delta": "LATE_TEXT"}),
            StreamEvent("thinking", {"delta": "LATE_THINKING"}),
            StreamEvent("tool_call", {"tool": "LATE_TOOL"}),
            StreamEvent("final", {"status": "failed", "output": "LATE_FINAL"}),
        ])
        self.assertEqual(adapter.replies, [
            ("正在处理…", False), ("临时预览", False), ("已完成", True),
        ])
        self.assertEqual((row.processing_status, row.delivery_status), ("completed", "sent"))

    async def test_presentation_no_tool_answer_keeps_text_throttling_and_flushes_final(self):
        with patch("deep_agent.channels.service.time", SimpleNamespace(monotonic=lambda: 100.0)):
            _, _, adapter, _, _ = await self._run_presentation([
                StreamEvent("text", {"delta": "甲"}),
                StreamEvent("text", {"delta": "乙"}),
                StreamEvent("text", {"delta": "丙"}),
                StreamEvent("final", {"status": "completed", "output": "甲乙丙"}),
            ], stream_interval=1)
        self.assertEqual(adapter.replies, [
            ("正在处理…", False), ("甲", False), ("甲乙丙", True),
        ])

    async def test_presentation_transport_error_after_final_does_not_overwrite_completed_answer(self):
        _, _, adapter, row, _ = await self._run_presentation([
            StreamEvent("text", {"delta": "中间过程"}),
            StreamEvent("final", {"status": "completed", "output": "最终答案"}),
            ConnectionError("fixture SSE disconnected after terminal event"),
        ])
        self.assertEqual(adapter.replies[-1], ("最终答案", True))
        self.assertEqual((row.processing_status, row.delivery_status), ("completed", "sent"))

    async def test_presentation_progress_transport_failure_does_not_abort_or_repeat_agent(self):
        for number, failure in enumerate(("loading", "preview", "wait")):
            with self.subTest(failure=failure):
                adapter = PresentationWeComClient(**{f"fail_{failure}": True})
                service, agent, adapter, row, message = await self._run_presentation([
                    StreamEvent("text", {"delta": "临时预览"}),
                    StreamEvent("tool_call", {"tool": "some_tool"}),
                    StreamEvent("text", {"delta": "另一个预览"}),
                    StreamEvent("final", {"status": "completed", "output": "最终答案"}),
                ], adapter=adapter, message_id=f"progress-error-{number}")
                self.assertEqual(adapter.replies[-1], ("最终答案", True))
                self.assertEqual((row.processing_status, row.delivery_status), ("completed", "sent"))
                self.assertEqual(sum(finish for _, _, finish in adapter.attempts), 1)
                await service.receive(self.account, adapter, message)
                self.assertEqual(agent.calls, 1)
                self.assertEqual(len(adapter.attempts), {"loading": 2, "preview": 3, "wait": 4}[failure])

    async def test_presentation_final_delivery_failure_is_durable_without_reexecuting_agent(self):
        service, agent, adapter, row, message = await self._run_presentation([
            StreamEvent("text", {"delta": "中间过程"}),
            StreamEvent("final", {"status": "completed", "output": "最终答案"}),
        ], adapter=PresentationWeComClient(fail_final=True))
        self.assertEqual((row.processing_status, row.delivery_status), ("completed", "failed"))
        self.assertEqual(row.error_code, "delivery_failed")
        self.assertIsNotNone(row.finished_at)
        self.assertEqual(adapter.attempts[-1], ("reply-id", "最终答案", True))
        before = list(adapter.attempts)
        await service.receive(self.account, adapter, message)
        self.assertEqual(agent.calls, 1)
        self.assertEqual(adapter.attempts, before)

    async def test_presentation_terminal_notice_closes_loading_without_private_details(self):
        cases = [
            (StreamEvent("cancelled", {"status": "cancelled"}), "interrupted", "已停止"),
            (StreamEvent("error", {"message": "PRIVATE_EXCEPTION"}), "failed", "失败"),
            (StreamEvent("final", {"status": "failed", "output": ""}), "failed", "失败"),
            (StreamEvent("final", {"status": "pending", "output": None}), "completed", "控制台"),
            (RuntimeError("PRIVATE_EXCEPTION"), "failed", "失败"),
        ]
        for number, (terminal, processing, notice) in enumerate(cases):
            with self.subTest(terminal=terminal):
                _, _, adapter, row, _ = await self._run_presentation([
                    StreamEvent("text", {"delta": "工具前的过程"}),
                    StreamEvent("tool_call", {"tool": "PRIVATE_TOOL"}),
                    terminal,
                ], message_id=f"terminal-{number}")
                final_text, finish = adapter.replies[-1]
                self.assertTrue(finish)
                self.assertIn(notice, final_text)
                self.assertNotIn("PRIVATE_", final_text)
                self.assertNotIn("工具前的过程", final_text)
                self.assertEqual((row.processing_status, row.delivery_status), (processing, "sent"))

    async def test_binding_reuses_session_and_rotates_after_agent_change_or_delete(self) -> None:
        first = await self.store.resolve_binding(
            account_id=self.account.id,
            peer_type="single",
            peer_id="user-1",
        )
        second = await self.store.resolve_binding(
            account_id=self.account.id,
            peer_type="single",
            peer_id="user-1",
        )
        self.assertEqual(first.session.session_id, second.session.session_id)
        self.assertFalse(second.created_session)
        self.assertEqual(first.binding.updated_at, second.binding.updated_at)
        self.assertFalse(hasattr(second.binding, "generation"))
        self.assertFalse(hasattr(second.binding, "last_active_at"))

        await self.store.update_account(
            self.account.id,
            ChannelAccountUpdate(agent_id="00000000-0000-0000-0000-000000000002"),
        )
        rotated = await self.store.resolve_binding(
            account_id=self.account.id,
            peer_type="single",
            peer_id="user-1",
        )
        self.assertNotEqual(rotated.session.session_id, first.session.session_id)
        self.assertEqual(rotated.binding.id, first.binding.id)

        async with self.factory.begin() as db:
            await db.execute(
                delete(ChatSession).where(
                    ChatSession.session_id == rotated.session.session_id
                )
            )
        recreated = await self.store.resolve_binding(
            account_id=self.account.id,
            peer_type="single",
            peer_id="user-1",
        )
        self.assertNotEqual(recreated.session.session_id, rotated.session.session_id)
        self.assertEqual(recreated.binding.id, first.binding.id)

    async def test_session_user_change_creates_session_without_reassigning_old_history(self):
        first = await self.store.resolve_binding(account_id=self.account.id, peer_type="single", peer_id="u")
        await self.store.update_account(self.account.id, ChannelAccountUpdate(session_user_id="operator"))
        second = await self.store.resolve_binding(account_id=self.account.id, peer_type="single", peer_id="u")
        self.assertNotEqual(first.session.session_id, second.session.session_id)
        self.assertEqual(second.session.user_id, "operator")
        async with self.factory() as db:
            old = await db.get(ChatSession, first.session.session_id)
            self.assertEqual(old.user_id, "owner")

    async def test_restart_does_not_retry_dispatched_request_without_sse_run_id(self):
        binding = await self.store.resolve_binding(account_id=self.account.id, peer_type="single", peer_id="u")
        fields = dict(account_id=self.account.id, binding_id=binding.binding.id,
                      external_message_id="lost-sse", external_request_id="r", sender_id="u",
                      session_id=binding.session.session_id, payload_hash="a" * 64)
        first = await self.store.claim_message(**fields)
        await self.store.update_run(first.run.id, processing_status="running")
        await self.store.interrupt_incomplete()
        self.assertFalse((await self.store.claim_message(**fields)).claimed)
        fields["external_message_id"] = "before-http"
        await self.store.claim_message(**fields)
        await self.store.interrupt_incomplete()
        self.assertTrue((await self.store.claim_message(**fields)).claimed)

    def test_deepagent_headers_only_carry_owner_and_internal_auth(self) -> None:
        config = GatewayConfig(
            api_url="http://127.0.0.1:8000",
            internal_token="local-test",
            poll_interval=3,
            stream_interval=1,
            test_timeout=3,
            host="127.0.0.1",
            port=8010,
            platform_authorization="",
            platform_cookie="",
        )
        headers = DeepAgentChannelClient(config)._headers(self.account)
        self.assertEqual(
            headers,
            {
                "user": "owner",
                "X-Deep-Agent-Channel-Token": "local-test",
                "X-Deep-Agent-Channel-Account": self.account.id,
            },
        )

    async def test_message_claim_is_idempotent_and_only_unknown_pre_run_can_retry(self) -> None:
        binding = await self.store.resolve_binding(
            account_id=self.account.id,
            peer_type="group",
            peer_id="chat-1",
        )
        fields = dict(
            account_id=self.account.id,
            binding_id=binding.binding.id,
            external_message_id="msg-1",
            external_request_id="req-1",
            sender_id="user-1",
            session_id=binding.session.session_id,
            payload_hash="a" * 64,
        )
        first = await self.store.claim_message(**fields)
        duplicate = await self.store.claim_message(**fields)
        self.assertTrue(first.claimed)
        self.assertFalse(duplicate.claimed)

        await self.store.update_run(
            first.run.id,
            processing_status="failed",
            delivery_status="failed",
            error_code="preflight_failed",
        )
        retry = await self.store.claim_message(**fields)
        self.assertTrue(retry.claimed)
        self.assertEqual(retry.run.attempt_count, 2)
        await self.store.update_run(
            retry.run.id,
            processing_status="running",
            agent_run_id="agent-run",
        )
        await self.store.update_run(retry.run.id, processing_status="interrupted")
        unknown_result = await self.store.claim_message(**fields)
        self.assertFalse(unknown_result.claimed)

    async def test_text_message_streams_only_agent_text_and_completes_run(self) -> None:
        frame = {
            "headers": {"req_id": "req-1"},
            "body": {
                "msgtype": "text",
                "msgid": "msg-stream",
                "from": {"userid": "user-1"},
                "text": {"content": "你好"},
            },
        }
        message = parse_message(frame)
        config = GatewayConfig(
            api_url="http://127.0.0.1:8000",
            internal_token="local-test",
            poll_interval=3,
            stream_interval=999,
            test_timeout=3,
            host="127.0.0.1",
            port=8010,
            platform_authorization="",
            platform_cookie="",
        )
        service = ChannelService(self.store, FakeDeepAgentClient(), stream_interval=999)
        client = FakeWeComClient()
        statements = []
        def record(_conn, _cursor, statement, _parameters, _context, _many):
            statements.append(statement.upper())
        event.listen(self.engine.sync_engine, "before_cursor_execute", record)
        try:
            await service.receive(self.account, client, message)
            await service.peers[(self.account.id, message.peer_type, message.peer_id)].worker
            run_updates = [s for s in statements if s.startswith("UPDATE CHANNEL_MESSAGE_RUNS")]
            self.assertLessEqual(len(run_updates), 6)  # queue/session/dispatch/id/result/delivery; never per token
            statements.clear()
            await service.receive(self.account, client, message)
            self.assertFalse(any(s.startswith(("UPDATE ", "INSERT ")) for s in statements))
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", record)
        self.assertEqual(client.replies[0], ("正在处理…", False))
        self.assertEqual(client.replies[-1], ("你好", True))
        self.assertNotIn("hidden", "".join(content for content, _ in client.replies))
        row = await self.store.get_message(self.account.id, "msg-stream")
        self.assertEqual((row.processing_status, row.delivery_status), ("completed", "sent"))
        self.assertIsNotNone(row.finished_at)
        self.assertIsNotNone(row.created_at)
        self.assertFalse(hasattr(row, "updated_at"))

    def test_peer_type_records_upstream_id_key(self):
        body = {"msgtype": "text", "msgid": "m", "chattype": "single",
                "chatid": "ignored-private-chat", "from": {"userid": "u"}, "text": {"content": "hello"}}
        message = parse_message({"body": body})
        self.assertEqual((message.peer_type, message.peer_id), ("single", "u"))
        body.update(chattype="group", chatid="g")
        message = parse_message({"body": body})
        self.assertEqual((message.peer_type, message.peer_id), ("group", "g"))
        body.pop("chatid")
        with self.assertRaises(ValueError):
            parse_message({"body": body})

    def test_parser_supports_group_mixed_and_generic_video(self) -> None:
        mixed = parse_message(
            {
                "headers": {"req_id": "r"},
                "body": {
                    "msgtype": "mixed",
                    "msgid": "m",
                    "chatid": "group-1",
                    "from": {"userid": "u"},
                    "mixed": {
                        "msg_item": [
                            {"msgtype": "text", "text": {"content": "说明"}},
                            {"msgtype": "image", "image": {"url": "https://media", "aeskey": "key"}},
                        ]
                    },
                },
            }
        )
        self.assertEqual((mixed.peer_type, mixed.peer_id, mixed.text), ("group", "group-1", "说明"))
        self.assertEqual(mixed.media[0].kind, "image")
        video = parse_message(
            {
                "headers": {"req_id": "r2"},
                "body": {
                    "msgtype": "video",
                    "msgid": "m2",
                    "from": {"userid": "u"},
                    "video": {"url": "https://media", "aeskey": "key"},
                },
            }
        )
        self.assertEqual(video.media[0].mime, "video/mp4")


class ChannelGatewayApiTests(unittest.IsolatedAsyncioTestCase):
    async def test_internal_routes_require_token_and_do_not_expect_context_query(self) -> None:
        app = create_gateway_app()
        runtime = SimpleNamespace(
            status_payload=lambda: {
                "state": "connected",
                "message": "ok",
                "accounts": [],
            },
            test_account=AsyncMock(
                return_value={
                    "account_id": "account-1",
                    "success": True,
                    "state": "connected",
                    "message": "ok",
                    "warning": None,
                }
            ),
        )
        app.state.gateway = SimpleNamespace(
            config=SimpleNamespace(internal_token="gateway-token"),
            runtime=runtime,
            database_revision="fixture-revision",
        )
        transport = ASGITransport(app=app, raise_app_exceptions=False)
        async with AsyncClient(transport=transport, base_url="http://gateway") as client:
            missing = await client.get("/health")
            health = await client.get(
                "/health", headers={"Authorization": "Bearer gateway-token"}
            )
            statuses = await client.get(
                "/accounts/status",
                headers={"Authorization": "Bearer gateway-token"},
            )
        self.assertEqual(missing.status_code, 401)
        self.assertEqual(health.status_code, 200, health.text)
        self.assertEqual(health.json()["database_revision"], "fixture-revision")
        self.assertEqual(statuses.json()["state"], "connected")


if __name__ == "__main__":
    unittest.main()
