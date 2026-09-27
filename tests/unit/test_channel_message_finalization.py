from __future__ import annotations

import hashlib
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

from deep_agent.channels.contracts import InboundMessage
from deep_agent.channels.gateway.deepagent_client import StreamEvent
from deep_agent.channels.service import ChannelService, MessageJob


class ChannelMessageFinalizationTests(unittest.IsolatedAsyncioTestCase):
    async def process(self, channel, *, final_output, sent=True, sent_text=None,
                      status="completed", preview="", reply_text="", omit_output=False):
        account = N(id="account", enabled=True, settings={}, channel_type=channel,
                    agent_id="agent", session_user_id="user")
        adapter = N(reply=AsyncMock(), reply_loading=AsyncMock())
        message = InboundMessage("text", "inbound", None, "sender", "single", "sender", {}, "hello")
        job = MessageJob(account, adapter, message, "channel-run", "reply", 1)
        store = N(get_account=AsyncMock(return_value=account), update_run=AsyncMock(),
                  resolve_binding=AsyncMock(return_value=N(account=account,
                    session=N(session_id="session", agent_id="agent"))),
                  has_user_messages=AsyncMock(return_value=True))

        async def stream(*args, **kwargs):
            yield StreamEvent("session", {"run_id": "agent-run"})
            if preview:
                yield StreamEvent("text", {"delta": preview})
            yield StreamEvent("tool_call", {"tool": "channel_message"})
            # The operation service only sets these markers after a confirmed
            # platform send. Exercise the real streaming consumer thereafter.
            job.message_tool_sent = sent
            if sent and sent_text is not None:
                job.sent_text_hashes.add(hashlib.sha256(sent_text.strip().encode()).hexdigest())
            if reply_text:
                yield StreamEvent("text", {"delta": reply_text})
            payload = {"status": status}
            if not omit_output:
                payload["output"] = final_output
            yield StreamEvent("final", payload)

        service = ChannelService(store, N(stream_chat=stream), stream_interval=0)
        await service._process(job)
        self.assertTrue(job.terminal)
        adapter.reply_loading.assert_awaited()
        finals = [call.args for call in adapter.reply.await_args_list if call.args[-1] is True]
        self.assertEqual(len(finals), 1)
        self.assertEqual(finals[0][1], job.reply_id)
        self.assertTrue(any(call.kwargs.get("delivery_status") == "sent"
                            for call in store.update_run.await_args_list))
        return finals[0][2], adapter

    async def test_pure_media_success_finishes_loading_without_empty_result_warning(self):
        for channel in ("feishu", "wecom"):
            for omit_output in (False, True):
                with self.subTest(channel=channel, legacy=omit_output):
                    output, _ = await self.process(channel, final_output="", preview="准备发送图片", omit_output=omit_output)
                    self.assertEqual(output, "已发送。")

    async def test_media_success_keeps_subsequent_answer_and_stream_preview(self):
        for channel in ("feishu", "wecom"):
            with self.subTest(channel=channel):
                output, adapter = await self.process(channel, final_output="图片已发送，这是结果说明。",
                                                    preview="正在准备图片", reply_text="图片已发送，这是结果说明。")
                self.assertEqual(output, "图片已发送，这是结果说明。")
                self.assertTrue(any(call.args[2] == "正在准备图片" and call.args[-1] is False
                                    for call in adapter.reply.await_args_list))
                self.assertEqual(adapter.reply_loading.await_args_list[-1].args[2], "正在准备图片")
                self.assertTrue(any(call.args[2] == "正在准备图片\n\n图片已发送，这是结果说明。"
                                    and call.args[-1] is False for call in adapter.reply.await_args_list))

    async def test_only_whole_matching_sent_text_is_suppressed(self):
        for channel in ("feishu", "wecom"):
            for answer, expected in (("相同正文", "已发送。"), ("相同正文，补充解释", "相同正文，补充解释"),
                                     ("说明：相同正文", "说明：相同正文"), ("相同\n正文", "相同\n正文")):
                with self.subTest(channel=channel, answer=answer):
                    output, _ = await self.process(channel, final_output=answer, sent_text="相同正文")
                    self.assertEqual(output, expected)

    async def test_unconfirmed_send_does_not_claim_success_or_suppress_answer(self):
        output, _ = await self.process("wecom", final_output="", sent=False)
        self.assertEqual(output, "已处理完成，但没有可展示的文本结果。")
        output, _ = await self.process("feishu", final_output="待发送正文", sent=False, sent_text="待发送正文")
        self.assertEqual(output, "待发送正文")

    async def test_partial_success_does_not_hide_failure_or_interruption(self):
        for status, expected in (("failed", "处理失败，请在 DeepAgent 控制台查看详情。"),
                                 ("interrupted", "已停止。")):
            with self.subTest(status=status):
                output, _ = await self.process("wecom", final_output="", status=status)
                self.assertEqual(output, expected)


if __name__ == "__main__":
    unittest.main()
