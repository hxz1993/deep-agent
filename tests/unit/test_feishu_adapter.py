from __future__ import annotations

import asyncio
import io
import logging
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from lark_channel.channel.types import (
    Conversation, Identity, InboundMessage as SDKMessage, TextContent,
    ResourceDescriptor, ReplyRef, SendResult, Mention,
)
from deep_agent.channels.adapters.feishu.core import FeishuSDKAdapter as FeishuAdapter
from deep_agent.channels.adapters.feishu.messages import MediaHandle, ReplyHandle, parse_message
from deep_agent.channels.adapters.feishu.sdk_logging import configure_sdk_logging
from deep_agent.channels.contracts import InboundMedia


def account(**settings):
    return SimpleNamespace(id="account", channel_type="feishu", transport="websocket",
                           bot_id="cli_test", secret="dummy-secret", settings={"feishu": settings})


def sdk_message(**changes):
    values = dict(id="om_message", create_time=1700000000000,
                  conversation=Conversation("oc_chat", "p2p"),
                  sender=Identity("ou_sender", user_id="u1", union_id="on_1", display_name="甲", sender_type="user"),
                  content=TextContent(text="hello"), content_text="hello", body_text="hello",
                  raw_content_type="text", raw={"message_id": "om_message", "content": '{"text":"hello"}'})
    values.update(changes)
    return SDKMessage(**values)


class FeishuMessagesTest(unittest.TestCase):
    def test_private_identity_uses_sender_and_keeps_native_chat_for_reply(self):
        result = parse_message(sdk_message(), envelope={"tenant_key": "tenant", "event_id": "event"})
        self.assertEqual((result.peer_type, result.peer_id, result.sender_id), ("single", "ou_sender", "ou_sender"))
        self.assertEqual(result.reply_handle, ReplyHandle("om_message", "oc_chat"))
        self.assertEqual(result.sender_name, "甲")
        self.assertEqual(result.peer_metadata["display_name"], "甲")
        self.assertEqual(result.tenant_id, "tenant")
        self.assertEqual(result.source_metadata, {
            "lark_user_id": "u1", "lark_union_id": "on_1",
            "lark_chat_id": "oc_chat", "lark_chat_type": "p2p",
            "lark_sender_type": "user",
        })
        self.assertEqual(result.request_id, "event")

    def test_group_thread_and_sender_are_distinct(self):
        result = parse_message(sdk_message(conversation=Conversation("oc_group", "group", "omt_thread")))
        self.assertEqual((result.peer_type, result.peer_id, result.thread_id), ("group", "oc_group", "omt_thread"))
        self.assertNotIn("display_name", result.peer_metadata)
        self.assertEqual(result.reply_handle.thread_id, "omt_thread")

    def test_opaque_ids_stay_when_profile_missing(self):
        result = parse_message(sdk_message(sender=Identity("ou_encrypted")))
        self.assertEqual(result.sender_id, "ou_encrypted")
        self.assertIsNone(result.sender_name)

    def test_hash_ignores_fetched_display_name_but_not_content(self):
        first = parse_message(sdk_message())
        second = parse_message(sdk_message(sender=Identity("ou_sender", display_name="Updated")))
        self.assertEqual(first.payload_hash, second.payload_hash)
        self.assertNotEqual(first.payload_hash, parse_message(sdk_message(content_text="changed")).payload_hash)

    def test_unsupported_empty_and_sticker_messages_do_not_start_runs(self):
        from lark_channel.channel.types import StickerContent, UnknownContent
        for source in (sdk_message(content=StickerContent(file_key="sticker")),
                       sdk_message(content=UnknownContent()),
                       sdk_message(content_text="", body_text="", content=TextContent(text=""))):
            with self.assertRaises(ValueError):
                parse_message(source)

    def test_missing_ids_and_unknown_chat_type_rejected(self):
        for msg in (sdk_message(id=""), sdk_message(sender=Identity("")),
                    sdk_message(conversation=Conversation("oc_chat", "unknown"))):
            with self.assertRaises(ValueError):
                parse_message(msg)

    def test_resources_and_quoted_media_use_separate_scoped_handles(self):
        source = sdk_message(resources=[ResourceDescriptor("audio", "file_audio"),
                                       ResourceDescriptor("file", "file_sheet", "财务.xlsx")])
        quoted = sdk_message(id="om_original", resources=[ResourceDescriptor("image", "img_orig")], content_text="old")
        result = parse_message(source, quote=quoted)
        self.assertEqual(result.media[0].kind, "voice")
        self.assertEqual(result.media[1].filename, "财务.xlsx")
        self.assertEqual(result.quote.text, "old")
        self.assertEqual(result.quote.media[0].handle.message_id, "om_original")
        self.assertEqual(result.text, "hello")


class FeishuAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = FeishuAdapter(account())
        self.sdk = self.adapter.client
        self.sdk.start_background = AsyncMock()
        self.sdk.stop_background = AsyncMock()
        self.sdk.finish_streaming_card = AsyncMock()
        self.sdk.add_reaction = AsyncMock(return_value=SendResult.ok(raw={"data": {"reaction_id": "reaction"}}))
        self.sdk.remove_reaction = AsyncMock(return_value=SendResult.ok())
        self.sdk.client.cardkit.v1.card_element.adelete = AsyncMock(
            return_value=SimpleNamespace(success=lambda: True))

    async def asyncTearDown(self):
        await self.adapter.stop()

    async def test_lifecycle_public_start_once_stop_and_late_status(self):
        callback = AsyncMock()
        await self.adapter.start(callback)
        await self.adapter.start(callback)
        await self.adapter._connect_task
        self.sdk.start_background.assert_awaited_once_with(timeout=30)
        self.assertTrue(self.adapter.status()["authenticated"])
        await self.adapter.stop()
        self.adapter._set_state("connected", "late")
        self.assertEqual(self.adapter.status()["state"], "closed")
        self.assertIsNone(self.adapter._connect_task)

    async def test_rest_connection_check_never_starts_or_consumes_websocket(self):
        self.adapter._probe_credentials = AsyncMock(return_value=True)
        result = await self.adapter.test_connection(1)
        self.assertTrue(result["success"])
        self.sdk.start_background.assert_not_called()
        self.assertEqual(self.adapter.status()["state"], "closed")
        self.adapter._probe_credentials.side_effect = RuntimeError("secret leaked")
        result = await self.adapter.test_connection(1)
        self.assertFalse(result["success"])
        self.assertNotIn("secret leaked", str(result))

    async def test_connect_error_does_not_leak_credentials_and_closes_sdk(self):
        self.adapter._infinite_retry = False
        self.sdk.start_background.side_effect = RuntimeError("wss://secret?token=private")
        await self.adapter.start(AsyncMock())
        await self.adapter._connect_task
        self.assertEqual(self.adapter.status()["state"], "failed")
        self.assertNotIn("private", str(self.adapter.status()))
        self.sdk.stop_background.assert_awaited()

    async def test_cross_loop_receive_dispatches_on_gateway_loop_and_waits_admission(self):
        loop = asyncio.get_running_loop()
        callbacks = []
        async def receive(message):
            self.assertIs(asyncio.get_running_loop(), loop)
            callbacks.append(message)
        await self.adapter.start(receive)
        self.adapter._capture_envelope({"header": {"event_id": "evt"}, "event": {
            "sender": {"tenant_key": "tenant"}, "message": {"message_id": "om_message"}}})
        await asyncio.to_thread(lambda: asyncio.run(self.adapter._receive(sdk_message())))
        self.assertEqual(len(callbacks), 1)
        self.assertEqual(callbacks[0].tenant_id, "tenant")
        self.assertEqual(len(self.adapter._pending), 0)
        await self.adapter.stop()
        await self.adapter._receive(sdk_message())
        self.assertEqual(len(callbacks), 1)

    async def test_stop_drains_inflight_admission(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def receive(_message):
            entered.set()
            await release.wait()
        await self.adapter.start(receive)
        incoming = asyncio.create_task(self.adapter._receive(sdk_message()))
        await entered.wait()
        stopping = asyncio.create_task(self.adapter.stop())
        await asyncio.sleep(0)
        self.assertFalse(stopping.done())
        release.set()
        await asyncio.gather(stopping, incoming)
        self.assertFalse(self.adapter._pending)

    async def test_quote_failure_and_cross_chat_quote_do_not_break_current_message(self):
        callback = AsyncMock()
        await self.adapter.start(callback)
        source = sdk_message(reply=ReplyRef(message_id="om_parent"))
        self.sdk.fetch_inbound_message = AsyncMock(return_value=sdk_message(conversation=Conversation("oc_other", "p2p")))
        await self.adapter._receive(source)
        self.assertIsNone(callback.call_args.args[0].quote)
        self.sdk.fetch_inbound_message.side_effect = RuntimeError("permission")
        await self.adapter._receive(source)
        self.assertEqual(callback.await_count, 2)
        self.assertIsNone(callback.call_args.args[0].quote)

    async def test_flat_reply_parent_equal_root_is_still_read_as_quote(self):
        callback = AsyncMock()
        await self.adapter.start(callback)
        source = sdk_message(raw={"parent_id": "om_parent", "root_id": "om_parent"})
        self.sdk.fetch_inbound_message = AsyncMock(return_value=sdk_message(id="om_parent", content_text="original"))
        await self.adapter._receive(source)
        self.sdk.fetch_inbound_message.assert_awaited_once_with("om_parent")
        self.assertEqual(callback.call_args.args[0].quote.text, "original")

    async def test_media_download_uses_message_resource_not_remote_url(self):
        self.sdk.download_resource = AsyncMock(return_value=b"data")
        media = InboundMedia("file", "data.txt", "text/plain", MediaHandle("om", "key", "file"))
        self.assertEqual(await self.adapter.download_media(media), (b"data", "data.txt"))
        self.sdk.download_resource.assert_awaited_once_with("key", "file", message_id="om")
        with self.assertRaises(ValueError):
            await self.adapter.download_media(InboundMedia("file", "f", "x", "https://bad"))
        self.sdk.download_resource.side_effect = RuntimeError("token secret")
        with self.assertRaisesRegex(RuntimeError, "^Feishu media download failed$"):
            await self.adapter.download_media(media)

    async def test_reaction_permission_failure_visible_without_losing_connection_or_leaking_hint(self):
        from deep_agent.channels.adapters.feishu.core import ReplyState
        self.adapter._started = True
        self.adapter._set_state("connected", "已连接飞书")
        for code in (99991672, 99991679):
            self.sdk.add_reaction.return_value = SimpleNamespace(success=False,
                raw={"code": code, "msg": "private token and message"},
                error=SimpleNamespace(raw_code=code, hint="private token and message"))
            await self.adapter._add_processing_reaction(ReplyState(message_id="om_message"))
            status = self.adapter.status()
            self.assertEqual(status["state"], "connected")
            self.assertTrue(status["authenticated"])
            self.assertIn(str(code), status["message"])
            self.assertIn("im:message.reactions:write_only", status["message"])
            self.assertNotIn("private", str(status))
        self.sdk.add_reaction.return_value = SendResult.ok(raw={"data": {"reaction_id": "r"}})
        await self.adapter._add_processing_reaction(ReplyState(message_id="om_message"))
        self.assertEqual(self.adapter.status()["message"], "已连接飞书")

    async def test_reaction_exception_and_malformed_response_have_safe_diagnostics(self):
        from deep_agent.channels.adapters.feishu.core import ReplyState
        self.adapter._started = True
        self.adapter._set_state("connected", "已连接飞书")
        self.sdk.add_reaction.side_effect = RuntimeError("private credential")
        await self.adapter._add_processing_reaction(ReplyState(message_id="om_message"))
        self.assertIn("请求不可用", self.adapter.status()["message"])
        self.assertNotIn("private", str(self.adapter.status()))
        self.sdk.add_reaction.side_effect = None
        self.sdk.add_reaction.return_value = SendResult.ok(raw={"data": {}})
        await self.adapter._add_processing_reaction(ReplyState(message_id="om_message"))
        self.assertIn("响应无效", self.adapter.status()["message"])

    async def test_processing_reaction_added_once_removed_on_finish_even_delivery_failure(self):
        self.sdk.create_card_instance = AsyncMock(side_effect=RuntimeError("no cards"))
        self.sdk.send = AsyncMock(side_effect=RuntimeError("send failed"))
        message = parse_message(sdk_message())
        await self.adapter.reply_loading(message, "r")
        await self.adapter.reply(message, "r", "partial", False)
        self.sdk.add_reaction.assert_awaited_once_with("om_message", "Get")
        with self.assertRaises(RuntimeError):
            await self.adapter.reply(message, "r", "final", True)
        self.sdk.remove_reaction.assert_awaited_once_with("om_message", "reaction")
        self.assertFalse(self.adapter._replies)

    async def test_reaction_timeout_does_not_block_static_final_reply(self):
        self.adapter.reaction_timeout = 0.001
        async def blocked(*_args):
            await asyncio.Future()
        self.sdk.add_reaction.side_effect = blocked
        self.sdk.create_card_instance = AsyncMock(side_effect=RuntimeError("no cards"))
        self.sdk.send = AsyncMock(return_value=SendResult.ok(message_id="reply"))
        message = parse_message(sdk_message())
        await self.adapter.reply_loading(message, "r")
        await self.adapter.reply(message, "r", "final", True)
        self.sdk.send.assert_awaited_once()
        self.sdk.remove_reaction.assert_not_called()
        self.assertFalse(self.adapter._replies)

    async def test_reaction_cleanup_timeout_does_not_fail_final_delivery(self):
        self.adapter.reaction_timeout = 0.001
        async def blocked(*_args):
            await asyncio.Future()
        self.sdk.remove_reaction.side_effect = blocked
        self.sdk.create_card_instance = AsyncMock(side_effect=RuntimeError("no cards"))
        self.sdk.send = AsyncMock(return_value=SendResult.ok(message_id="reply"))
        message = parse_message(sdk_message())
        await self.adapter.reply_loading(message, "r")
        await self.adapter.reply(message, "r", "final", True)
        self.sdk.send.assert_awaited_once()
        self.sdk.remove_reaction.assert_awaited_once()
        self.assertFalse(self.adapter._replies)

    async def test_stop_cleans_processing_reaction_and_terminal_only_reply_does_not_add_it(self):
        self.sdk.create_card_instance = AsyncMock(side_effect=RuntimeError("no cards"))
        self.sdk.send = AsyncMock(return_value=SendResult.ok(message_id="reply"))
        message = parse_message(sdk_message())
        await self.adapter.reply(message, "direct", "final", True)
        self.sdk.add_reaction.assert_not_called()
        await self.adapter.reply_loading(message, "active")
        await self.adapter.stop()
        self.sdk.remove_reaction.assert_awaited_once_with("om_message", "reaction")
        self.assertFalse(self.adapter._replies)
        await self.adapter.stop()
        self.sdk.remove_reaction.assert_awaited_once()

    async def test_card_progress_and_finish_use_one_card_and_monotonic_sequence(self):
        self.sdk.create_card_instance = AsyncMock(return_value="card")
        self.sdk.send_card_by_reference = AsyncMock(return_value=SendResult.ok(message_id="om_reply"))
        self.sdk.update_card_element_content = AsyncMock()
        self.sdk.finish_streaming_card = AsyncMock()
        message = parse_message(sdk_message(conversation=Conversation("oc_chat", "group", "omt_thread")))
        await self.adapter.reply_loading(message, "reply")
        await self.adapter.reply(message, "reply", "answer", False)
        await self.adapter.reply(message, "reply", "final", True)
        self.sdk.create_card_instance.assert_awaited_once()
        self.sdk.send_card_by_reference.assert_awaited_once_with("oc_chat", "card", receive_id_type="chat_id",
                  reply_to="om_message", reply_in_thread=True, reply_target_gone="fail")
        self.assertEqual([call.args[-1] for call in self.sdk.update_card_element_content.await_args_list], [1, 2])
        self.sdk.finish_streaming_card.assert_awaited_once_with("card", 3)
        removal = self.sdk.client.cardkit.v1.card_element.adelete.call_args.args[0]
        self.assertEqual(removal.paths, {"card_id": "card", "element_id": "loading_icon"})
        self.assertEqual(removal.body.sequence, 4)
        self.assertFalse(self.adapter._replies)

    async def test_loading_footer_survives_stream_and_wait_then_is_removed(self):
        self.sdk.create_card_instance = AsyncMock(return_value="card")
        self.sdk.send_card_by_reference = AsyncMock(return_value=SendResult.ok(message_id="om_reply"))
        self.sdk.update_card_element_content = AsyncMock()
        self.sdk.finish_streaming_card = AsyncMock()
        message = parse_message(sdk_message())
        await self.adapter.reply_loading(message, "r")
        card = self.sdk.create_card_instance.call_args.args[0]
        self.assertTrue(card["config"]["streaming_mode"])
        footer = card["body"]["elements"][-1]
        self.assertEqual(footer["element_id"], "loading_icon")
        self.assertEqual(footer["icon"]["tag"], "custom_icon")
        self.assertEqual(footer["icon"]["size"], "16px 16px")
        self.assertEqual(card["body"]["elements"][0]["content"], "")
        self.sdk.update_card_element_content.assert_not_awaited()
        await self.adapter.reply(message, "r", "第一段正文", False)
        await self.adapter.reply_loading(message, "r", "第一段正文")
        self.sdk.update_card_element_content.assert_awaited_with("card", "answer", "第一段正文", 2)
        self.sdk.finish_streaming_card.assert_not_awaited()
        self.sdk.client.cardkit.v1.card_element.adelete.assert_not_awaited()
        await self.adapter.reply(message, "r", "最终正文", True)
        self.sdk.update_card_element_content.assert_awaited_with("card", "answer", "最终正文", 3)
        self.sdk.finish_streaming_card.assert_awaited_once_with("card", 4)
        self.assertEqual(self.sdk.client.cardkit.v1.card_element.adelete.call_args.args[0].body.sequence, 5)
        self.sdk.send_card_by_reference.assert_awaited_once()

    async def test_stop_clears_visible_loading_without_blocking_transport_shutdown(self):
        self.sdk.create_card_instance = AsyncMock(return_value="card")
        self.sdk.send_card_by_reference = AsyncMock(return_value=SendResult.ok(message_id="om_reply"))
        await self.adapter.reply_loading(parse_message(sdk_message()), "r")
        self.sdk.client.cardkit.v1.card_element.adelete.side_effect = RuntimeError("private")
        await self.adapter.stop()
        self.sdk.finish_streaming_card.assert_awaited_once_with("card", 1)
        self.sdk.client.cardkit.v1.card_element.adelete.assert_awaited_once()
        self.sdk.stop_background.assert_awaited_once()
        self.assertFalse(self.adapter._replies)

    async def test_terminal_delivery_failure_still_clears_loading_footer(self):
        self.sdk.create_card_instance = AsyncMock(return_value="card")
        self.sdk.send_card_by_reference = AsyncMock(return_value=SendResult.ok(message_id="om_reply"))
        self.sdk.update_card_element_content = AsyncMock(side_effect=RuntimeError("private"))
        self.sdk.finish_streaming_card = AsyncMock()
        message = parse_message(sdk_message())
        await self.adapter.reply_loading(message, "r")
        with self.assertRaisesRegex(RuntimeError, '^Feishu reply failed$'):
            await self.adapter.reply(message, "r", "执行失败", True)
        self.sdk.finish_streaming_card.assert_awaited_once_with("card", 2)
        self.assertEqual(self.sdk.client.cardkit.v1.card_element.adelete.call_args.args[0].body.sequence, 3)
        self.assertFalse(self.adapter._replies)

    async def test_cancelled_reply_clears_footer_even_when_stream_close_fails(self):
        self.sdk.create_card_instance = AsyncMock(return_value="card")
        self.sdk.send_card_by_reference = AsyncMock(return_value=SendResult.ok(message_id="om_reply"))
        self.sdk.update_card_element_content = AsyncMock()
        self.sdk.finish_streaming_card = AsyncMock(side_effect=RuntimeError("private"))
        message = parse_message(sdk_message())
        await self.adapter.reply_loading(message, "r")
        with self.assertRaisesRegex(RuntimeError, '^Feishu reply failed$'):
            await self.adapter.reply(message, "r", "已停止", True)
        self.sdk.client.cardkit.v1.card_element.adelete.assert_awaited_once()
        self.assertFalse(self.adapter._replies)
        self.sdk.send_card_by_reference.assert_awaited_once()

    async def test_without_card_permission_final_markdown_still_sent_once(self):
        self.sdk.create_card_instance = AsyncMock(side_effect=RuntimeError("forbidden"))
        self.sdk.send = AsyncMock(return_value=SendResult.ok(message_id="reply"))
        msg = parse_message(sdk_message())
        await self.adapter.reply_loading(msg, "r")
        self.sdk.send.assert_not_called()
        await self.adapter.reply(msg, "r", "answer", True)
        self.sdk.send.assert_awaited_once()
        self.assertFalse(self.adapter._replies)

    async def test_send_failure_after_card_creation_does_not_send_duplicate_fallback(self):
        self.sdk.create_card_instance = AsyncMock(return_value="card")
        self.sdk.send_card_by_reference = AsyncMock(side_effect=TimeoutError())
        self.sdk.send = AsyncMock()
        with self.assertRaisesRegex(RuntimeError, "Feishu reply failed"):
            await self.adapter.reply(parse_message(sdk_message()), "r", "answer", True)
        self.sdk.send.assert_not_called()
        self.assertFalse(self.adapter._replies)

    async def test_uncertain_preview_send_is_not_repeated_by_final_delivery(self):
        self.sdk.create_card_instance = AsyncMock(return_value="card")
        self.sdk.send_card_by_reference = AsyncMock(side_effect=TimeoutError())
        self.sdk.send = AsyncMock()
        message = parse_message(sdk_message())
        with self.assertRaisesRegex(RuntimeError, "Feishu reply failed"):
            await self.adapter.reply_loading(message, "r")
        with self.assertRaisesRegex(RuntimeError, "Feishu reply failed"):
            await self.adapter.reply(message, "r", "final", True)
        self.sdk.send_card_by_reference.assert_awaited_once()
        self.sdk.send.assert_not_called()
        self.assertFalse(self.adapter._replies)

    def test_gateway_owns_message_batching_sdk_settings_remain_bounded(self):
        config = self.sdk.config
        self.assertEqual(config.transport.kind, "ws")
        self.assertTrue(config.policy.require_mention)
        self.assertFalse(config.safety.chat_queue.enabled)
        self.assertEqual(config.safety.text_batch.max_messages, 1)
        self.assertFalse(config.media_cache.enabled)
        self.assertEqual(config.security.max_concurrent_ws_handlers, 64)

    def test_envelope_metadata_has_bounded_retention(self):
        for index in range(500):
            self.adapter._capture_envelope({"event": {"message": {"message_id": str(index)}}})
        self.assertEqual(len(self.adapter._envelopes), self.adapter.max_pending * 2)

    def test_sdk_logs_never_include_payload_exception_or_url(self):
        configure_sdk_logging()
        capture = io.StringIO()
        handler = logging.StreamHandler(capture)
        log = logging.getLogger("Lark")
        log.addHandler(handler)
        try:
            try:
                raise ValueError("secret-and-user-message")
            except ValueError:
                log.exception("Failed %s", "wss://private?token=secret")
        finally:
            log.removeHandler(handler)
        self.assertNotIn("secret", capture.getvalue())
        self.assertNotIn("private", capture.getvalue())


class FeishuRealSDKBoundaryTest(unittest.IsolatedAsyncioTestCase):
    async def test_official_sdk_reaction_result_extraction_and_remove(self):
        from deep_agent.channels.adapters.feishu.core import ReplyState
        adapter = FeishuAdapter(account())
        state = ReplyState(message_id="om_message")
        with patch.object(adapter.client.driver, "add_reaction", AsyncMock(return_value={
                "code": 0, "data": {"reaction_id": "reaction"}})) as add, \
             patch.object(adapter.client.driver, "remove_reaction", AsyncMock(return_value={"code": 0})) as remove:
            await adapter._add_processing_reaction(state)
            self.assertEqual(state.reaction_id, "reaction")
            await adapter._remove_processing_reaction(state)
            add.assert_awaited_once_with(message_id="om_message", emoji_type="Get")
            remove.assert_awaited_once_with(message_id="om_message", reaction_id="reaction")
            self.assertIsNone(state.reaction_id)

    async def test_official_sdk_card_serialization_and_reference_route(self):
        adapter = FeishuAdapter(account())
        driver = adapter.client.driver
        with patch.object(driver, "cardkit_create", AsyncMock(return_value={"code": 0, "data": {"card_id": "card"}})) as create, \
             patch.object(driver, "cardkit_update_element", AsyncMock(return_value={"code": 0})) as update, \
             patch.object(driver, "cardkit_update_settings", AsyncMock(return_value={"code": 0})) as finish, \
             patch.object(adapter.client.client.im.v1.message, "areply", AsyncMock(return_value=SimpleNamespace(code=0, msg="", data=SimpleNamespace(message_id="om_reply")))) as reply:
            await adapter.reply(parse_message(sdk_message()), "r", "你好", True)
            import json
            card = json.loads(create.call_args.kwargs["body"]["data"])
            self.assertEqual(card["schema"], "2.0")
            self.assertEqual(card["config"]["streaming_config"], {
                "print_frequency_ms": {"default": 50},
                "print_step": {"default": 2}, "print_strategy": "fast",
            })
            self.assertEqual(card["body"]["elements"][0]["content"], "你好")
            request = reply.call_args.args[0]
            self.assertEqual(request.paths["message_id"], "om_message")
            self.assertEqual(request.body.msg_type, "interactive")
            self.assertEqual(json.loads(request.body.content)["data"]["card_id"], "card")
            self.assertEqual(update.call_args.kwargs["body"]["sequence"], 1)
            self.assertEqual(finish.call_args.kwargs["body"]["sequence"], 2)

class FeishuSDKHTTPTest(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_secret_probe_uses_real_sdk_http_and_no_cached_token(self):
        import httpx
        calls = []
        async def handler(request):
            calls.append(request)
            if request.url.path.endswith("/tenant_access_token/internal"):
                self.assertEqual(__import__('json').loads(request.content)["app_secret"], "dummy-secret")
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "fresh-token", "expire": 7200})
            self.assertEqual(request.headers["Authorization"], "Bearer fresh-token")
            return httpx.Response(200, json={"code": 0, "bot": {"open_id": "ou_bot"}})
        factory = httpx.AsyncClient
        transport = httpx.MockTransport(handler)
        adapter = FeishuAdapter(account())
        with patch("lark_channel.core.http.transport.httpx.AsyncClient", side_effect=lambda **kw: factory(transport=transport)):
            result = await adapter.test_connection(2)
        self.assertTrue(result["success"])
        self.assertEqual(len(calls), 2)
        self.assertEqual(adapter.status()["state"], "closed")

    async def test_invalid_secret_never_uses_old_bot_identity(self):
        import httpx
        factory = httpx.AsyncClient
        calls = []
        async def handler(request):
            calls.append(request)
            return httpx.Response(200, json={"code": 10014, "msg": "invalid app_secret"})
        adapter = FeishuAdapter(account())
        with patch("lark_channel.core.http.transport.httpx.AsyncClient", side_effect=lambda **kw: factory(transport=httpx.MockTransport(handler))):
            result = await adapter.test_connection(2)
        self.assertFalse(result["success"])
        self.assertEqual(len(calls), 1)

    async def test_initial_transient_start_failure_is_retried_without_new_adapter(self):
        adapter = FeishuAdapter(account())
        adapter.client.start_background = AsyncMock(side_effect=[TimeoutError(), None])
        adapter.client.stop_background = AsyncMock()
        with patch("deep_agent.channels.adapters.feishu.core.asyncio.sleep", AsyncMock()) as sleep:
            await adapter.start(AsyncMock())
            await adapter._connect_task
        self.assertEqual(adapter.client.start_background.await_count, 2)
        sleep.assert_awaited_once_with(1.0)
        self.assertTrue(adapter.status()["authenticated"])
        await adapter.stop()

class FeishuSDKInboundTest(unittest.IsolatedAsyncioTestCase):
    async def test_default_template_bot_and_document_events_do_not_trigger_agent(self):
        from lark_channel.api.im.v1.model.p2_im_chat_member_bot_added_v1 import P2ImChatMemberBotAddedV1
        from lark_channel.api.im.v1.model.p2_im_chat_member_bot_deleted_v1 import P2ImChatMemberBotDeletedV1
        from lark_channel.event.custom import CustomizedEvent
        adapter = FeishuAdapter(account())
        adapter._started = True
        adapter._loop = asyncio.get_running_loop()
        adapter._callback = AsyncMock()
        observed = AsyncMock()
        for event_name in ('botAdded', 'botLeave', 'comment'):
            adapter.client.on(event_name, observed)
        payload = {'event': {'chat_id': 'oc_fixture', 'operator_id': {'open_id': 'ou_fixture'}}}
        await adapter.client._handle_bot_event(P2ImChatMemberBotAddedV1(payload), joined=True)
        await adapter.client._handle_bot_event(P2ImChatMemberBotDeletedV1(payload), joined=False)
        await adapter.client._handle_comment_event(CustomizedEvent({'event': {
            'file_token': 'doc_fixture', 'file_type': 'docx', 'comment_id': 'comment_fixture',
            'user_id': {'open_id': 'ou_fixture'}, 'is_mentioned': True,
        }}))
        self.assertEqual(observed.await_count, 3)
        adapter._callback.assert_not_awaited()
        self.assertFalse(adapter._pending)
        self.assertFalse(adapter._envelopes)

    async def test_sdk_read_and_reaction_notifications_never_enter_agent_message_callback(self):
        from lark_channel.api.im.v1.model.p2_im_message_reaction_created_v1 import P2ImMessageReactionCreatedV1
        from lark_channel.api.im.v1.model.p2_im_message_reaction_deleted_v1 import P2ImMessageReactionDeletedV1
        from lark_channel.api.im.v1.model.p2_im_message_message_read_v1 import P2ImMessageMessageReadV1
        adapter = FeishuAdapter(account())
        adapter._started = True
        adapter._loop = asyncio.get_running_loop()
        adapter._callback = AsyncMock()
        payload = {"event": {"message_id": "om_fixture", "user_id": {"open_id": "ou_user"},
                             "reaction_type": {"emoji_type": "Get"}}}
        await adapter.client._handle_reaction_event(P2ImMessageReactionCreatedV1(payload), action="create")
        await adapter.client._handle_reaction_event(P2ImMessageReactionDeletedV1(payload), action="delete")
        await adapter.client._handle_message_read_event(P2ImMessageMessageReadV1({
            "event": {"message_id_list": ["om_fixture"]}}))
        adapter._callback.assert_not_awaited()
        self.assertFalse(adapter._pending)
        self.assertFalse(adapter._envelopes)

    async def test_real_sdk_normalizes_message_fetch_and_attached_file(self):
        from lark_channel import FeishuChannel
        channel = FeishuChannel(app_id="cli_test", app_secret="dummy", name_lookup=lambda _ids: {})
        raw = {"code": 0, "data": {"items": [{
            "message_id": "om_source", "chat_id": "oc_chat", "chat_type": "p2p", "msg_type": "file",
            "create_time": "1700000000000", "sender": {"id": "ou_person", "id_type": "open_id", "sender_type": "user"},
            "body": {"content": '{"file_key":"file_key","file_name":"季度.xlsx"}'},
        }]}}
        with patch.object(channel.driver, "fetch_message", AsyncMock(return_value=raw)):
            normalized = await channel.fetch_inbound_message("om_source")
        result = parse_message(normalized)
        self.assertEqual(result.sender_id, "ou_person")
        self.assertEqual(result.media[0].filename, "季度.xlsx")
        self.assertEqual(result.media[0].handle, MediaHandle("om_source", "file_key", "file"))
        self.assertEqual(result.peer_type, "single")

    async def test_real_sdk_group_policy_mention_and_self_filters(self):
        import time
        from lark_channel.channel.safety.pipeline import SafetyPipeline
        from lark_channel.channel.config import ChatQueueConfig, DedupConfig
        adapter = FeishuAdapter(account(group_allowlist=["oc_allowed"]))
        delivered, rejected = [], []
        async def receive(message):
            delivered.append(message.id)
        pipeline = SafetyPipeline(loop=asyncio.get_running_loop(), on_message=receive,
                                  on_reject=lambda event: rejected.append(event.reason),
                                  policy=adapter.client.config.policy,
                                  dedup_config=DedupConfig(enabled=False, ttl_seconds=0, max_entries=0),
                                  queue_config=ChatQueueConfig(enabled=False))
        pipeline.set_bot_open_id("ou_bot")
        def msg(mid, chat="oc_allowed", mention=False, sender="ou_sender"):
            return sdk_message(id=mid, create_time=int(time.time()*1000),
                               conversation=Conversation(chat, "group"), sender=Identity(sender), mentioned_bot=mention,
                               mentions=[Mention("@_user_1", open_id="ou_bot")] if mention else [])
        await pipeline.push_message(msg("unmentioned"))
        await pipeline.push_message(msg("wronggroup", chat="oc_other", mention=True))
        await pipeline.push_message(msg("self", mention=True, sender="ou_bot"))
        await pipeline.push_message(msg("valid", mention=True))
        # No SDK SeenCache retention: durable gateway admission decides retries.
        await pipeline.push_message(msg("valid", mention=True))
        self.assertEqual(delivered, ["valid", "valid"])
        self.assertIn("policy_no_mention", rejected)
        self.assertIn("policy_group_not_in_allowlist", rejected)
        self.assertIn("self_sent", rejected)


class FeishuCoreFailureTest(unittest.IsolatedAsyncioTestCase):
    async def test_close_failure_does_not_start_duplicate_sdk(self):
        adapter = FeishuAdapter(account())
        adapter.client.start_background = AsyncMock(side_effect=TimeoutError())
        adapter.client.stop_background = AsyncMock(side_effect=RuntimeError("private"))
        await adapter.start(AsyncMock())
        await adapter._connect_task
        adapter.client.start_background.assert_awaited_once()
        self.assertEqual(adapter.status()["state"], "failed")
        with self.assertRaisesRegex(RuntimeError, "Feishu connection close failed"):
            await adapter.stop()

    async def test_stop_cancels_start_and_unresponsive_admission(self):
        adapter = FeishuAdapter(account())
        entered = asyncio.Event()
        async def blocked():
            entered.set()
            await asyncio.Future()
        adapter.client.start_background = AsyncMock(side_effect=lambda **kw: blocked())
        # AsyncMock returning a coroutine would not await it; use the real async function.
        async def start(**kw):
            await blocked()
        adapter.client.start_background = start
        adapter.client.stop_background = AsyncMock()
        await adapter.start(AsyncMock())
        await entered.wait()
        pending = asyncio.create_task(asyncio.sleep(60))
        adapter._pending.add(pending)
        adapter.close_timeout = 0.001
        await adapter.stop()
        self.assertTrue(pending.cancelled())
        self.assertIsNone(adapter._connect_task)

    async def test_admission_overload_and_bad_message_are_bounded(self):
        adapter = FeishuAdapter(account())
        adapter._started = True
        adapter._loop = asyncio.get_running_loop()
        adapter._callback = AsyncMock()
        adapter._slots = __import__('threading').BoundedSemaphore(0)
        await adapter._receive(sdk_message())
        adapter._callback.assert_not_called()
        await adapter._admit(sdk_message(id=""), {})
        adapter._callback.assert_not_called()
        adapter._capture_envelope({})
        self.assertFalse(adapter._envelopes)

    async def test_empty_download_invalid_reply_and_capacity_failure(self):
        adapter = FeishuAdapter(account())
        adapter.client.download_resource = AsyncMock(return_value=b"")
        with self.assertRaisesRegex(RuntimeError, "download failed"):
            await adapter.download_media(InboundMedia("file", "f", "x", MediaHandle("om", "key", "file")))
        from dataclasses import replace
        with self.assertRaises(ValueError):
            await adapter.reply(replace(parse_message(sdk_message()), reply_handle=None), "r", "x", True)
        adapter.max_pending = 0
        with self.assertRaisesRegex(RuntimeError, "capacity"):
            await adapter.reply(parse_message(sdk_message()), "r", "x", True)
        self.assertNotEqual(adapter.new_reply_id(), adapter.new_reply_id())

    async def test_rest_probe_rejects_malformed_token_and_bot_failure(self):
        import json
        adapter = FeishuAdapter(account())
        def response(body, success=True):
            return SimpleNamespace(success=lambda: success, raw=SimpleNamespace(content=json.dumps(body).encode()))
        adapter.client.client.arequest = AsyncMock(return_value=response({}))
        self.assertFalse(await adapter._probe_credentials())
        adapter.client.client.arequest.side_effect = [response({"tenant_access_token": "token"}), response({}, False)]
        self.assertFalse(await adapter._probe_credentials())


if __name__ == "__main__":
    unittest.main()
