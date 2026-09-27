from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aibot import WSClient, WSClientOptions
from deep_agent.channels.adapters.wecom.sdk_logger import SafeSdkLogger

from deep_agent.channels.adapters.wecom import WecomAdapter, outbound
from deep_agent.channels.outbound import DeliveryError, MessageTarget, PreparedComponent


class RecordingClient:
    def __init__(self):
        self.calls = []
        self.send_message = AsyncMock(return_value={"errcode": 0, "headers": {"req_id": "send-ack"}})
        self.chunk_error = None

    async def reply(self, frame, body, cmd=None):
        self.calls.append((frame, body, cmd))
        result = {}
        if cmd == "aibot_upload_media_init":
            result = {"upload_id": "upload-test"}
        elif cmd == "aibot_upload_media_finish":
            result = {"media_id": "media-test"}
        elif self.chunk_error:
            error, self.chunk_error = self.chunk_error, None
            raise error
        return {"errcode": 0, "body": result, "headers": frame["headers"]}


class WecomOutboundTests(unittest.IsolatedAsyncioTestCase):
    def test_image_formats_only_advertise_verified_native_formats(self):
        self.assertEqual(outbound.message_capabilities().image_formats, ("png", "jpeg"))

    def test_actual_image_containers_downgrade_through_wecom_capabilities(self):
        from PIL import Image
        from deep_agent.channels.message_tool import normalize_media
        for fmt, expected in (("GIF", "file"), ("WEBP", "file"), ("BMP", "file"), ("PNG", "image"), ("JPEG", "image")):
            with self.subTest(format=fmt):
                data = io.BytesIO()
                Image.new("RGB", (8, 8), "blue").save(data, format=fmt)
                component = PreparedComponent("image", data=data.getvalue(), filename="example." + fmt.lower(), requested_kind="image")
                normalized = normalize_media(component, outbound.message_capabilities())
                self.assertEqual(normalized.kind, expected)
                self.assertEqual(normalized.requested_kind, "image")
                self.assertEqual(normalized.data, component.data)

    async def test_three_step_upload_uses_distinct_ids_exact_bytes_and_zero_based_chunks(self):
        client = RecordingClient()
        data = b"a" * outbound.CHUNK_SIZE + b"final-chunk"
        receipt = await outbound.send_component(client, "group-1", PreparedComponent("file", data=data, filename="report.txt"))
        self.assertEqual(receipt.kind, "file")
        self.assertEqual(receipt.request_id, "send-ack")
        self.assertIsNone(receipt.message_id)
        self.assertEqual([c[2] for c in client.calls], [
            "aibot_upload_media_init", "aibot_upload_media_chunk", "aibot_upload_media_chunk", "aibot_upload_media_finish",
        ])
        init = client.calls[0][1]
        self.assertEqual(init, {"type": "file", "filename": "report.txt", "total_size": len(data), "total_chunks": 2, "md5": hashlib.md5(data).hexdigest()})
        chunks = [c[1] for c in client.calls[1:-1]]
        self.assertEqual([c["chunk_index"] for c in chunks], [0, 1])
        self.assertEqual(b"".join(base64.b64decode(c["base64_data"]) for c in chunks), data)
        self.assertEqual(len({c[0]["headers"]["req_id"] for c in client.calls}), 4)
        client.send_message.assert_awaited_once_with("group-1", {"msgtype": "file", "file": {"media_id": "media-test"}})

    async def test_each_component_has_native_wire_type_and_no_passive_reply(self):
        for kind, native in [("image", "image"), ("audio", "voice"), ("video", "video")]:
            client = RecordingClient()
            receipt = await outbound.send_component(client, "user-1", PreparedComponent(kind, data=b"media", filename="sample"))
            self.assertEqual(receipt.kind, kind)
            self.assertEqual(client.calls[0][1]["type"], native)
            client.send_message.assert_awaited_once_with("user-1", {"msgtype": native, native: {"media_id": "media-test"}})
        client = RecordingClient()
        await outbound.send_component(client, "user-1", PreparedComponent("text", text="**hello**"))
        self.assertEqual(client.calls, [])
        client.send_message.assert_awaited_once_with("user-1", {"msgtype": "markdown", "markdown": {"content": "**hello**"}})

    async def test_chunk_timeout_retries_same_bytes_with_new_request_id(self):
        client = RecordingClient()
        client.chunk_error = TimeoutError("secret")
        with patch.object(outbound, "RETRY_DELAY", 0):
            await outbound.send_component(client, "user", PreparedComponent("image", data=b"data", filename="x.png"))
        self.assertEqual(client.calls[1][1], client.calls[2][1])
        self.assertNotEqual(client.calls[1][0], client.calls[2][0])
        self.assertEqual(len(client.calls), 4)
        client.send_message.assert_awaited_once()

    async def test_failed_upload_and_missing_media_id_never_send(self):
        for response in [{"errcode": 1, "errmsg": "secret"}, {"errcode": 0, "body": {}}, {}]:
            client = RecordingClient()
            client.reply = AsyncMock(return_value=response)
            with self.assertRaises(DeliveryError) as error:
                await outbound.send_component(client, "user", PreparedComponent("file", data=b"data", filename="x.txt"))
            self.assertFalse(error.exception.unknown)
            self.assertNotIn("secret", str(error.exception))
            client.send_message.assert_not_awaited()

    async def test_chunk_retries_are_bounded_and_never_finish_failed_upload(self):
        client = RecordingClient()
        native = client.reply
        async def fail_chunk(frame, body, cmd=None):
            if cmd == "aibot_upload_media_chunk":
                client.calls.append((frame, body, cmd))
                raise TimeoutError("secret")
            return await native(frame, body, cmd)
        client.reply = fail_chunk
        with patch.object(outbound, "RETRY_DELAY", 0), self.assertRaises(DeliveryError):
            await outbound.send_component(client, "user", PreparedComponent("file", data=b"data", filename="x"))
        self.assertEqual([c[2] for c in client.calls].count("aibot_upload_media_chunk"), 3)
        self.assertNotIn("aibot_upload_media_finish", [c[2] for c in client.calls])
        client.send_message.assert_not_awaited()

    async def test_send_timeout_disconnect_malformed_ack_are_unknown_and_never_retried(self):
        for failure in [TimeoutError("secret"), RuntimeError("Connection closed: secret"), ValueError("secret")]:
            client = RecordingClient()
            client.send_message.side_effect = failure
            with self.assertRaises(DeliveryError) as error:
                await outbound.send_component(client, "user", PreparedComponent("text", text="hello"))
            self.assertTrue(error.exception.unknown)
            self.assertNotIn("secret", str(error.exception))
            client.send_message.assert_awaited_once()
        client = RecordingClient()
        client.send_message.return_value = {}
        with self.assertRaises(DeliveryError) as error:
            await outbound.send_component(client, "user", PreparedComponent("text", text="hello"))
        self.assertTrue(error.exception.unknown)

    async def test_negative_sdk_ack_is_known_failure_without_raw_error(self):
        client = RecordingClient()
        client.send_message.side_effect = RuntimeError("Reply ack error: errcode=40014, errmsg=secret")
        with self.assertRaises(DeliveryError) as error:
            await outbound.send_component(client, "user", PreparedComponent("text", text="hello"))
        self.assertFalse(error.exception.unknown)
        self.assertEqual(error.exception.code, "platform_rejected")
        self.assertNotIn("secret", str(error.exception))

    async def test_cancel_during_upload_does_not_finish_or_send(self):
        client = RecordingClient()
        started = asyncio.Event()
        async def blocked(*args, **kwargs):
            started.set()
            await asyncio.Future()
        client.reply = blocked
        task = asyncio.create_task(outbound.send_component(client, "user", PreparedComponent("file", data=b"data")))
        await started.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        client.send_message.assert_not_awaited()

    async def test_adapter_requires_matching_account_authenticated_and_no_quote(self):
        adapter = WecomAdapter.__new__(WecomAdapter)
        adapter.account = SimpleNamespace(id="account")
        adapter.client = RecordingClient()
        adapter._authenticated = asyncio.Event()
        component = PreparedComponent("text", text="hello")
        target = MessageTarget("account", "single", "user", "inbound-message")
        with self.assertRaises(DeliveryError):
            await adapter.send_component(target, component)
        adapter._authenticated.set()
        with self.assertRaises(DeliveryError):
            await adapter.send_component(MessageTarget("other", "single", "user", "inbound-message"), component)
        with self.assertRaises(DeliveryError):
            await adapter.send_component(target, component, reply_to_current=True)
        adapter.client.send_message.assert_not_awaited()
        self.assertEqual((await adapter.send_component(target, component)).request_id, "send-ack")

    async def test_sdk_public_reply_and_send_serialize_protocol_with_native_ack_queue(self):
        # Keep the installed SDK's queue, request IDs and ACK handling. Only the
        # socket write is replaced, so this detects public SDK API mismatches.
        client = WSClient(WSClientOptions(bot_id="test", secret="test", logger=SafeSdkLogger()))
        manager = client._ws_manager
        sent = []
        async def socket_send(frame):
            sent.append(frame)
            body = {}
            if frame["cmd"] == "aibot_upload_media_init":
                body = {"upload_id": "upload-test"}
            elif frame["cmd"] == "aibot_upload_media_finish":
                body = {"media_id": "media-test"}
            loop = asyncio.get_running_loop()
            loop.call_soon(manager._handle_reply_ack, frame["headers"]["req_id"], {
                "errcode": 0, "headers": frame["headers"], "body": body,
            })
        manager.send = socket_send
        try:
            receipt = await outbound.send_component(client, "user-test", PreparedComponent("image", data=b"image", filename="image.png"))
            self.assertTrue(receipt.request_id.startswith("aibot_send_msg"))
            self.assertEqual(sent[-1]["body"], {"chatid": "user-test", "msgtype": "image", "image": {"media_id": "media-test"}})
            self.assertEqual(len({frame["headers"]["req_id"] for frame in sent}), 4)
            self.assertEqual(manager._pending_acks, {})
        finally:
            client.disconnect()

    async def test_sdk_negative_upload_ack_is_not_retried(self):
        client = RecordingClient()
        native = client.reply
        async def reject_chunk(frame, body, cmd=None):
            if cmd == "aibot_upload_media_chunk":
                client.calls.append((frame, body, cmd))
                raise RuntimeError("Reply ack error: errcode=40014, errmsg=secret")
            return await native(frame, body, cmd)
        client.reply = reject_chunk
        with patch.object(outbound, "RETRY_DELAY", 0), self.assertRaises(DeliveryError) as error:
            await outbound.send_component(client, "user", PreparedComponent("file", data=b"data", filename="x"))
        self.assertEqual(error.exception.code, "platform_rejected")
        self.assertEqual(len(client.calls), 2)
        client.send_message.assert_not_awaited()

    async def test_invalid_media_size_rejected_before_upload(self):
        client = RecordingClient()
        with self.assertRaises(DeliveryError):
            await outbound.upload_media(client, b"", media_type="file", filename="x")
        with patch.object(outbound, "MAX_CHUNKS", 1), self.assertRaises(DeliveryError):
            await outbound.upload_media(client, b"x" * (outbound.CHUNK_SIZE + 1), media_type="file", filename="x")
        self.assertEqual(client.calls, [])


if __name__ == "__main__":
    unittest.main()
