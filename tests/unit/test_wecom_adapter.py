from __future__ import annotations

import asyncio
import inspect
import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from aibot import MessageHandler, WsCmd

from deep_agent.channels.adapters.wecom import WecomAdapter
from deep_agent.channels.adapters.wecom.messages import parse_message
from deep_agent.channels.adapters.wecom.sdk_logger import SafeSdkLogger
from deep_agent.channels.commands import parse_command


def frame(kind="text", payload=None, **body):
    return {
        "cmd": WsCmd.CALLBACK,
        "headers": {"req_id": "request-test"},
        "body": {
            "msgid": "message-test", "msgtype": kind,
            "chattype": "single", "from": {"userid": "sender-test", "name": "测试用户"},
            kind: payload if payload is not None else {"content": "你好"},
            **body,
        },
    }


class FakeSdkClient:
    """Only fake transport I/O; native MessageHandler still dispatches the frame."""

    def __init__(self, options):
        self.options = options
        self.handlers = {}
        self.pending = []
        self.is_connected = False
        self.connect = AsyncMock()
        self.disconnect = Mock()
        self.download_file = AsyncMock(return_value=(b"image-bytes", "image.jpg"))
        self.reply_stream = AsyncMock()

    def on(self, event, callback):
        self.handlers.setdefault(event, []).append(callback)

    def emit(self, event, *args):
        for callback in self.handlers.get(event, []):
            value = callback(*args)
            if inspect.isawaitable(value):
                self.pending.append(asyncio.create_task(value))

    async def flush(self):
        pending, self.pending = self.pending, []
        if pending:
            await asyncio.gather(*pending)


class WecomAdapterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.account = SimpleNamespace(
            id="account-test", channel_type="wecom", transport="websocket",
            bot_id="bot-test", secret="not-a-real-credential", agent_id="agent-one",
            session_user_id="session-user", name="企微测试", settings={},
        )
        self.factory = patch(
            "deep_agent.channels.adapters.wecom.WSClient", side_effect=FakeSdkClient,
        )
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.adapter = WecomAdapter(self.account)
        self.addAsyncCleanup(self.adapter.stop)

    async def test_uses_sdk_native_lifecycle_and_starts_only_one_client(self):
        callback = AsyncMock()
        await self.adapter.start(callback)
        await self.adapter.start(callback)
        await asyncio.sleep(0)
        self.adapter.client.connect.assert_awaited_once()
        self.assertEqual(self.adapter.client.options.max_reconnect_attempts, -1)
        self.assertIsInstance(self.adapter.client.options.logger, SafeSdkLogger)
        self.adapter.client.emit("connected")
        self.assertEqual(self.adapter.status()["state"], "connecting")
        self.assertFalse(self.adapter.status()["authenticated"])
        self.adapter.client.emit("authenticated")
        self.assertTrue(self.adapter.status()["authenticated"])
        self.assertTrue((await self.adapter.test_connection(0.01))["success"])
        self.adapter.client.emit("reconnecting", 1)
        self.assertEqual(self.adapter.status()["state"], "reconnecting")
        self.assertFalse(self.adapter.status()["authenticated"])
        self.adapter.client.emit("authenticated")
        self.adapter.client.emit("disconnected", "network closed")
        self.assertFalse(self.adapter.status()["authenticated"])
        await self.adapter.stop()
        self.adapter.client.disconnect.assert_called_once()
        self.adapter.client.emit("authenticated")
        self.assertEqual(self.adapter.status()["state"], "closed")

    async def test_connection_test_waits_for_sdk_and_never_builds_another_socket(self):
        await self.adapter.start(AsyncMock())
        result = await self.adapter.test_connection(0.001)
        self.assertFalse(result["success"])
        self.assertEqual(result["state"], "failed")
        self.adapter.client.connect.assert_awaited_once()
        self.adapter.client.emit("authenticated")
        self.assertTrue((await self.adapter.test_connection(0.001))["success"])
        temporary = WecomAdapter(self.account, infinite_retry=False)
        self.addAsyncCleanup(temporary.stop)
        self.assertEqual(temporary.client.options.max_reconnect_attempts, 0)

    async def test_stop_drains_callbacks_and_rejects_new_arrivals(self):
        entered = asyncio.Event()
        cancelled = asyncio.Event()

        async def callback(_message):
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        await self.adapter.start(callback)
        self.adapter.client.emit("message", frame())
        await asyncio.wait_for(entered.wait(), 1)
        await self.adapter.stop()
        self.assertTrue(cancelled.is_set())
        self.assertEqual(self.adapter._callback_tasks, set())
        self.assertTrue(all(task.done() for task in self.adapter.client.pending))
        callback_after = AsyncMock()
        self.adapter._on_message = callback_after
        await self.adapter._receive_message(frame())
        callback_after.assert_not_awaited()

    async def test_stop_drains_unsupported_message_reply_outside_common_service(self):
        entered = asyncio.Event()

        async def blocked_reply(*_args):
            entered.set()
            await asyncio.Event().wait()

        self.adapter.client.reply_stream.side_effect = blocked_reply
        await self.adapter.start(AsyncMock())
        self.adapter.client.emit("message", frame("unknown", {}))
        await asyncio.wait_for(entered.wait(), 1)
        await self.adapter.stop()
        self.assertEqual(self.adapter._callback_tasks, set())
        self.assertTrue(all(task.cancelled() for task in self.adapter.client.pending))

    async def test_callback_can_stop_its_adapter_without_cancelling_itself(self):
        finished = asyncio.Event()

        async def callback(_message):
            await self.adapter.stop()
            finished.set()

        await self.adapter.start(callback)
        self.adapter.client.emit("message", frame())
        await asyncio.wait_for(self.adapter.client.flush(), 1)
        self.assertTrue(finished.is_set())
        self.assertEqual(self.adapter._callback_tasks, set())

    async def test_stop_waits_for_sdk_public_connection_state(self):
        await self.adapter.start(AsyncMock())
        self.adapter.client.is_connected = True
        loop = asyncio.get_running_loop()
        self.adapter.client.disconnect.side_effect = lambda: loop.call_later(
            0.03, setattr, self.adapter.client, "is_connected", False,
        )
        await self.adapter.stop()
        self.assertFalse(self.adapter.client.is_connected)
        self.adapter.client.disconnect.assert_called_once()

    async def test_stop_has_bounded_safe_connection_close_failure(self):
        await self.adapter.start(AsyncMock())
        self.adapter.close_timeout = 0.001
        self.adapter.client.is_connected = True
        try:
            with self.assertRaisesRegex(RuntimeError, "^WeCom connection close timed out$"):
                await self.adapter.stop()
            self.assertEqual(self.adapter.status()["state"], "failed")
        finally:
            self.adapter.client.is_connected = False

    async def test_connect_errors_are_safe_and_sdk_retains_retry_responsibility(self):
        self.adapter.client.connect.side_effect = RuntimeError(
            "auth failure not-a-real-credential https://media.invalid/private"
        )
        await self.adapter.start(AsyncMock())
        await asyncio.sleep(0)
        status = self.adapter.status()
        self.assertEqual(status["state"], "failed")
        self.assertNotIn("not-a-real-credential", str(status))
        self.assertNotIn("https://", str(status))
        self.adapter.client.emit("error", RuntimeError("duplicate Bot connection"))
        self.assertEqual(self.adapter.status()["state"], "conflict")

    async def test_native_dispatch_delivers_once_including_generic_video(self):
        callback = AsyncMock()
        await self.adapter.start(callback)
        handler = MessageHandler(SafeSdkLogger())
        handler.handle_frame(frame(), self.adapter.client)
        await self.adapter.client.flush()
        callback.assert_awaited_once()
        self.assertNotIn("message.text", self.adapter.client.handlers)
        video = frame("video", {"url": "https://media.invalid/video", "aeskey": "test-key"})
        handler.handle_frame(video, self.adapter.client)
        await self.adapter.client.flush()
        self.assertEqual(callback.await_count, 2)
        self.assertEqual(callback.await_args.args[0].media[0].kind, "video")
        for event_type in ("enter_chat", "unrecognized-event"):
            event = frame("event", {"eventtype": event_type})
            event["cmd"] = WsCmd.EVENT_CALLBACK
            handler.handle_frame(event, self.adapter.client)
        await self.adapter.client.flush()
        self.assertEqual(callback.await_count, 2)
        self.adapter.client.reply_stream.assert_not_awaited()

    async def test_sdk_callbacks_do_not_serialize_different_messages(self):
        blocker = asyncio.Event()
        arrivals = []

        async def callback(message):
            arrivals.append(message.text)
            if message.text == "work":
                await blocker.wait()

        await self.adapter.start(callback)
        handler = MessageHandler(SafeSdkLogger())
        handler.handle_frame(frame(payload={"content": "work"}), self.adapter.client)
        handler.handle_frame(frame(payload={"content": "/stop"}), self.adapter.client)
        try:
            await asyncio.sleep(0)
            self.assertEqual(arrivals, ["work", "/stop"])
        finally:
            blocker.set()
            await self.adapter.client.flush()

    async def test_media_and_stream_reply_delegate_to_sdk_using_opaque_handles(self):
        message = parse_message(frame("image", {
            "url": "https://media.invalid/image", "aeskey": "opaque-test-key",
        }))
        self.assertFalse(hasattr(message, "frame"))
        self.assertFalse(hasattr(message.media[0], "url"))
        self.assertNotIn("opaque-test-key", repr(message.media[0].handle))
        self.assertNotIn("https://media", repr(message))
        self.assertEqual(
            await self.adapter.download_media(message.media[0]), (b"image-bytes", "image.jpg"),
        )
        self.adapter.client.download_file.assert_awaited_once_with(
            "https://media.invalid/image", "opaque-test-key",
        )
        reply_id = self.adapter.new_reply_id()
        self.assertTrue(reply_id.startswith("deepagent_"))
        await self.adapter.reply(message, reply_id, "正文", False)
        await self.adapter.reply(message, reply_id, "完整正文", True)
        self.adapter.client.reply_stream.assert_awaited_with(
            {"headers": {"req_id": "request-test"}}, reply_id, "完整正文", True,
        )

    async def test_sdk_exceptions_never_export_raw_frame_or_media_details(self):
        message = parse_message(frame("image", {"url": "https://media.invalid", "aeskey": "key"}))
        detail = "upstream failure https://media.invalid credential-content"
        self.adapter.client.download_file.side_effect = RuntimeError(detail)
        with self.assertRaisesRegex(RuntimeError, "^WeCom media download failed$"):
            await self.adapter.download_media(message.media[0])
        self.adapter.client.reply_stream.side_effect = RuntimeError(detail)
        with self.assertRaisesRegex(RuntimeError, "^WeCom reply failed$"):
            await self.adapter.reply(message, "reply", "text", True)
        with self.assertRaisesRegex(ValueError, "Invalid WeCom media handle"):
            await self.adapter.download_media(replace(message.media[0], handle=object()))

    async def test_unsupported_message_gets_safe_native_reply(self):
        callback = AsyncMock()
        await self.adapter.start(callback)
        self.adapter.client.emit("message", frame("unknown", {}))
        await self.adapter.client.flush()
        callback.assert_not_awaited()
        args = self.adapter.client.reply_stream.await_args.args
        self.assertEqual(args[0], {"headers": {"req_id": "request-test"}})
        self.assertTrue(args[-1])
        self.assertIn("暂不支持", args[2])

    def test_routing_config_does_not_change_connection_fingerprint(self):
        first = self.adapter.connection_fingerprint(self.account)
        self.account.agent_id = "agent-two"
        self.account.session_user_id = "another-user"
        self.account.name = "另一个名称"
        self.assertEqual(first, self.adapter.connection_fingerprint(self.account))
        self.account.secret = "another-test-credential"
        self.assertNotEqual(first, self.adapter.connection_fingerprint(self.account))


class WecomMessageTests(unittest.TestCase):
    def test_quote_keeps_original_text_separate_from_current_question(self):
        message = parse_message(frame(payload={"content": "解释这句"}, quote={
            "msgtype": "text", "text": {"content": "引用原文\n/stop"},
        }))
        self.assertEqual(message.text, "解释这句")
        self.assertEqual(message.quote.text, "引用原文\n/stop")
        self.assertEqual(message.quote.message_type, "text")
        self.assertFalse(message.command_eligible)
        self.assertIsNone(parse_command(message))

    def test_quoted_media_and_voice_use_the_existing_native_conversion(self):
        for kind in ("image", "file", "video"):
            with self.subTest(kind=kind):
                message = parse_message(frame(quote={"msgtype": kind, kind: {
                    "url": "https://media.invalid/quote", "aeskey": "quote-test-key",
                }}))
                self.assertEqual(message.quote.media[0].kind, kind)
                self.assertNotIn("quote-test-key", repr(message))
                self.assertNotIn("https://media.invalid", repr(message))
        voice = parse_message(frame(quote={"msgtype": "voice", "voice": {"content": "语音引用"}}))
        self.assertEqual(voice.quote.text, "语音引用")
        mixed = parse_message(frame(quote={"msgtype": "mixed", "mixed": {"msg_item": [
            {"msgtype": "text", "text": {"content": "图文说明"}},
            {"msgtype": "image", "image": {"url": "https://media.invalid/quote", "aeskey": "quote-key"}},
        ]}}))
        self.assertEqual(mixed.quote.text, "图文说明")
        self.assertEqual(len(mixed.quote.media), 1)

    def test_group_mentions_allow_commands_but_not_embedded_or_quoted_commands(self):
        for separator in (" ", "\u2005", "\u00a0", "\n"):
            for command in ("/help", "/new", "/stop", "/status", "/whoami"):
                with self.subTest(separator=separator, command=command):
                    message = parse_message(frame(payload={"content": f"@测试机器人{separator}{command}"},
                                                  chattype="group", chatid="group-test"))
                    self.assertIsNone(parse_command(message))
                    plain = parse_message(frame(payload={"content": command}, chattype="group", chatid="group-test"))
                    self.assertEqual(parse_command(plain), command)
        for text in ("@测试机器人 请解释 /stop", "正文 @测试机器人 /new", "@测试机器人 /help\n继续说明"):
            self.assertIsNone(parse_command(parse_message(frame(payload={"content": text},
                                                                chattype="group", chatid="group-test"))))
        spaced = parse_message(frame(payload={"content": "@Deep Agent\u2005/help"}, chattype="group", chatid="group-test"))
        self.assertEqual(spaced.text, "@Deep Agent\u2005/help")
        self.assertIsNone(parse_command(spaced))
        multiple = parse_message(frame(payload={"content": "@测试机器人 @其他成员 /stop"}, chattype="group", chatid="group-test"))
        self.assertIsNone(parse_command(multiple))
        self.assertIsNone(parse_command(parse_message(frame(payload={"content": "@测试机器人 /new"}))))
        quoted = parse_message(frame(payload={"content": "@测试机器人 /stop"}, chattype="group", chatid="group-test",
                                     quote={"msgtype": "text", "text": {"content": "旧消息"}}))
        self.assertIsNone(parse_command(quoted))
        unknown = parse_message(frame(payload={"content": "@测试机器人 /new model"}, chattype="group", chatid="group-test"))
        self.assertIsNone(parse_command(unknown))

    def test_native_identity_and_optional_display_names(self):
        private = parse_message(frame(chatid="private-id"))
        self.assertEqual((private.peer_type, private.peer_id), ("single", "sender-test"))
        self.assertEqual(private.peer_metadata, {"user_id": "sender-test", "chat_id": "private-id", "display_name": "测试用户"})
        group = parse_message(frame(chattype="group", chatid="group-id", chat_name="测试群"))
        self.assertEqual((group.peer_type, group.peer_id), ("group", "group-id"))
        self.assertEqual(group.peer_metadata, {"chat_id": "group-id", "display_name": "测试群"})
        with self.assertRaises(ValueError):
            parse_message(frame(chattype="group"))

    def test_media_shapes_mixed_and_transcribed_voice(self):
        for kind, mime in (("image", "image/jpeg"), ("file", "application/octet-stream"),
                           ("voice", "audio/amr"), ("video", "video/mp4")):
            message = parse_message(frame(kind, {"url": "https://media.invalid", "aeskey": "key"}))
            self.assertEqual(message.media[0].mime, mime)
            self.assertFalse(message.command_eligible)
        mixed = parse_message(frame("mixed", {"msg_item": [
            {"msgtype": "text", "text": {"content": "说明"}},
            {"msgtype": "image", "image": {"url": "https://media.invalid", "aeskey": "key"}},
        ]}))
        self.assertEqual(mixed.text, "说明")
        self.assertEqual(len(mixed.media), 1)
        self.assertFalse(mixed.command_eligible)
        voice = parse_message(frame("voice", {"content": "/new"}))
        self.assertEqual(voice.text, "/new")
        self.assertEqual(voice.media, ())
        self.assertFalse(voice.command_eligible)

    def test_only_plain_unquoted_text_can_be_a_command(self):
        self.assertTrue(parse_message(frame(payload={"content": "/help"})).command_eligible)
        quoted = frame(payload={"content": "/stop"}, quote={"msgtype": "text", "text": {"content": "old"}})
        self.assertFalse(parse_message(quoted).command_eligible)
        mixed = frame("mixed", {"msg_item": [{"msgtype": "text", "text": {"content": "/new"}}]})
        self.assertFalse(parse_message(mixed).command_eligible)

    def test_hash_has_no_body_or_media_material_and_errors_are_fixed(self):
        message = parse_message(frame())
        self.assertEqual(len(message.payload_hash), 64)
        self.assertEqual(message.payload_hash, parse_message(frame()).payload_hash)
        with self.assertRaisesRegex(ValueError, "^Unsupported WeCom message type$"):
            parse_message(frame("unexpected-private-data", {}))

    def test_sdk_logger_emits_only_fixed_labels(self):
        raw = "Authentication failed: credential-content https://media.invalid/private"
        with patch("deep_agent.channels.adapters.wecom.sdk_logger.logger") as output:
            safe = SafeSdkLogger()
            safe.info(raw, {"body": "private-body"})
            safe.warning(raw)
            safe.error(raw)
            safe.exception(raw)
            safe.debug(raw)
        serialized = str(output.mock_calls)
        self.assertIn("authentication event", serialized)
        self.assertNotIn("credential-content", serialized)
        self.assertNotIn("https://", serialized)
        self.assertNotIn("private-body", serialized)


if __name__ == "__main__":
    unittest.main()
