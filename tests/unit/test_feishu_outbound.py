"""Official request shapes, single-attempt delivery and isolated worker boundary."""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from deep_agent.channels.adapters.feishu import FeishuAdapter
from deep_agent.channels.adapters.feishu.outbound import message_capabilities, send_component
from deep_agent.channels.adapters.feishu.worker import _serve
from deep_agent.channels.outbound import ComponentReceipt, DeliveryError, MessageTarget, PreparedComponent


def target(**changes):
    return MessageTarget(**{"account_id": "a", "peer_type": "single", "peer_id": "ou_user",
                            "message_id": "om_inbound", "chat_id": "oc_current", **changes})


def client():
    result = SimpleNamespace(success=lambda: True, data=SimpleNamespace(message_id="om_sent"))
    messages = SimpleNamespace(acreate=AsyncMock(return_value=result), areply=AsyncMock(return_value=result))
    return SimpleNamespace(upload_media=AsyncMock(return_value="uploaded_key"),
                           client=SimpleNamespace(im=SimpleNamespace(v1=SimpleNamespace(message=messages))))


class OutboundTest(unittest.IsolatedAsyncioTestCase):
    def test_capabilities_are_identical_without_client(self):
        self.assertEqual(FeishuAdapter.message_capabilities(), message_capabilities())
        self.assertEqual(message_capabilities().audio_formats, ("opus",))

    async def test_text_to_original_chat_uses_one_official_create(self):
        sdk = client()
        receipt = await send_component(sdk, target(), PreparedComponent("text", text="你好"))
        request = sdk.client.im.v1.message.acreate.call_args.args[0]
        self.assertEqual(request.receive_id_type, "chat_id")
        self.assertEqual(request.request_body.receive_id, "oc_current")
        self.assertEqual(json.loads(request.body.content), {"text": "你好"})
        self.assertEqual(receipt, ComponentReceipt("text", message_id="om_sent"))
        sdk.upload_media.assert_not_awaited()
        sdk.client.im.v1.message.areply.assert_not_awaited()

    async def test_all_media_upload_first_and_use_native_message_type(self):
        for kind, wire in (("image", "image"), ("file", "file"), ("audio", "audio"), ("video", "media")):
            with self.subTest(kind=kind):
                sdk = client()
                await send_component(sdk, target(), PreparedComponent(kind, data=b"fixture", filename="sample.dat"))
                call = sdk.upload_media.call_args
                self.assertEqual(call.args[0].buffer, b"fixture")
                self.assertEqual(call.args[0].kind, "buffer")
                self.assertEqual(call.kwargs["file_name"], "sample.dat")
                self.assertEqual(call.kwargs["file_type"], {"image": None, "file": "stream", "audio": "opus", "video": "mp4"}[kind])
                request = sdk.client.im.v1.message.acreate.call_args.args[0]
                self.assertEqual(request.body.msg_type, wire)
                self.assertEqual(json.loads(request.body.content), {"image_key" if kind == "image" else "file_key": "uploaded_key"})
                sdk.client.im.v1.message.acreate.assert_awaited_once()

    async def test_quote_and_thread_inherit_trigger(self):
        for quote, thread in ((True, None), (False, "omt_current")):
            sdk = client()
            await send_component(sdk, target(thread_id=thread), PreparedComponent("file", data=b"x", filename="a.txt"), reply_to_current=quote)
            req = sdk.client.im.v1.message.areply.call_args.args[0]
            self.assertEqual(req.message_id, "om_inbound")
            self.assertEqual(req.body.reply_in_thread, bool(thread))
            sdk.client.im.v1.message.acreate.assert_not_awaited()

    async def test_invalid_target_is_rejected_before_upload(self):
        sdk = client()
        with self.assertRaises(DeliveryError):
            await send_component(sdk, target(chat_id=None), PreparedComponent("file", data=b"x"))
        sdk.upload_media.assert_not_awaited()

    async def test_upload_failure_is_known_failure_and_never_sends(self):
        sdk = client(); sdk.upload_media.side_effect = RuntimeError("secret raw payload")
        with self.assertRaises(DeliveryError) as caught:
            await send_component(sdk, target(), PreparedComponent("image", data=b"image"))
        self.assertEqual(caught.exception.code, "upload_failed")
        self.assertFalse(caught.exception.unknown)
        self.assertNotIn("secret", str(caught.exception))
        sdk.client.im.v1.message.acreate.assert_not_awaited()

    async def test_network_and_missing_receipt_are_unknown_without_retry(self):
        for mode in ("network", "missing"):
            sdk = client()
            if mode == "network":
                sdk.client.im.v1.message.acreate.side_effect = TimeoutError("private credential")
            else:
                sdk.client.im.v1.message.acreate.return_value.data.message_id = None
            with self.assertRaises(DeliveryError) as caught:
                await send_component(sdk, target(), PreparedComponent("text", text="test"))
            self.assertTrue(caught.exception.unknown)
            sdk.client.im.v1.message.acreate.assert_awaited_once()
            self.assertNotIn("private", str(caught.exception))

    async def test_permission_and_revoked_reply_are_not_retried_or_downgraded(self):
        for raw, expected in ((230003, "permission_denied"), (230002, "target_revoked")):
            sdk = client()
            sdk.client.im.v1.message.areply.return_value = SimpleNamespace(success=lambda: False, code=raw)
            with self.assertRaises(DeliveryError) as caught:
                await send_component(sdk, target(), PreparedComponent("text", text="test"), reply_to_current=True)
            self.assertEqual(caught.exception.code, expected)
            self.assertFalse(caught.exception.unknown)
            sdk.client.im.v1.message.areply.assert_awaited_once()
            sdk.client.im.v1.message.acreate.assert_not_awaited()

    async def test_upload_cancellation_never_reaches_send(self):
        sdk = client(); entered = asyncio.Event()
        async def upload(*args, **kwargs):
            entered.set(); await asyncio.Future()
        sdk.upload_media.side_effect = upload
        task = asyncio.create_task(send_component(sdk, target(), PreparedComponent("file", data=b"x")))
        await asyncio.wait_for(entered.wait(), 2); task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        sdk.client.im.v1.message.acreate.assert_not_awaited()


class OutboundIPCTest(unittest.IsolatedAsyncioTestCase):
    async def test_worker_whitelist_and_safe_error_roundtrip(self):
        parent, child = multiprocessing.get_context("spawn").Pipe()
        sdk = SimpleNamespace(status=lambda: {}, stop=AsyncMock(),
                              send_component=AsyncMock(return_value=ComponentReceipt("image", message_id="om_sent")))
        task = asyncio.create_task(_serve(child, sdk))
        async def call(identifier):
            await asyncio.to_thread(parent.send, {"kind": "call", "id": identifier, "method": "send_component",
                                                 "args": (target(), PreparedComponent("image", data=b"x"), True)})
            while True:
                packet = await asyncio.to_thread(parent.recv)
                if packet.get("kind") == "result":
                    return packet
        try:
            result = await call("ok")
            self.assertEqual(result["result"].message_id, "om_sent")
            self.assertTrue(sdk.send_component.call_args.kwargs["reply_to_current"])
            sdk.send_component.side_effect = DeliveryError("upload_failed", "上传失败")
            error = await call("bad")
            self.assertEqual(error["delivery_error"], {"code": "upload_failed", "message": "上传失败", "unknown": False})
            adapter = FeishuAdapter(SimpleNamespace(id="a"))
            process = Mock(); adapter._process = process
            future = asyncio.get_running_loop().create_future(); adapter._pending["bad"] = future
            adapter._dispatch(error, process)
            with self.assertRaises(DeliveryError) as caught:
                await future
            self.assertFalse(caught.exception.unknown)
        finally:
            parent.close()
            await asyncio.wait_for(task, 2)
            child.close()

    async def test_cancel_packet_stops_worker_upload_before_send(self):
        parent, child = multiprocessing.get_context("spawn").Pipe()
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def sending(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()
        sdk = SimpleNamespace(status=lambda: {}, stop=AsyncMock(), send_component=sending)
        task = asyncio.create_task(_serve(child, sdk))
        try:
            await asyncio.to_thread(parent.send, {"kind": "call", "id": "outgoing", "method": "send_component",
                                                 "args": (target(), PreparedComponent("image", data=b"x"), False)})
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.to_thread(parent.send, {"kind": "cancel", "id": "outgoing"})
            await asyncio.wait_for(cancelled.wait(), 2)
        finally:
            parent.close(); await asyncio.wait_for(task, 2); child.close()

    async def test_parent_cancel_sends_worker_cancel_packet(self):
        adapter = FeishuAdapter(SimpleNamespace(id="a")); adapter._started = True
        packets = []
        adapter._send = lambda packet: packets.append(packet)
        task = asyncio.create_task(adapter.send_component(target(), PreparedComponent("text", text="x")))
        async with asyncio.timeout(2):
            while not packets:
                await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual([packet["kind"] for packet in packets], ["call", "cancel"])
        self.assertEqual(packets[0]["id"], packets[1]["id"])
        self.assertFalse(adapter._pending)

    async def test_parent_timeout_becomes_unknown(self):
        adapter = FeishuAdapter(SimpleNamespace(id="a")); adapter._started = True
        adapter._rpc = AsyncMock(side_effect=RuntimeError("IPC unavailable"))
        with self.assertRaises(DeliveryError) as caught:
            await adapter.send_component(target(), PreparedComponent("text", text="x"))
        self.assertTrue(caught.exception.unknown)

    async def test_account_mismatch_never_calls_worker(self):
        adapter = FeishuAdapter(SimpleNamespace(id="other")); adapter._started = True
        adapter._rpc = AsyncMock()
        with self.assertRaises(DeliveryError):
            await adapter.send_component(target(), PreparedComponent("text", text="x"))
        adapter._rpc.assert_not_awaited()



class WorkerAdmissionTests(unittest.IsolatedAsyncioTestCase):
    async def test_burst_calls_count_toward_capacity_before_execution(self):
        packets = iter({"kind": "call", "id": str(i), "method": "send_component",
                        "args": (target(), PreparedComponent("text", text="x"), False)} for i in range(150))
        sent = []
        class Connection:
            def recv(self):
                try:
                    return next(packets)
                except StopIteration:
                    raise EOFError from None
            def send(self, packet):
                sent.append(packet)
        class Reader:
            def __init__(self, *, target, **kwargs):
                self.target = target
            def start(self):
                # Queue the full burst on one event-loop turn, before any call
                # coroutine has had an opportunity to register itself.
                self.target()
        sdk = SimpleNamespace(status=lambda: {}, stop=AsyncMock(), send_component=AsyncMock())
        with patch("deep_agent.channels.adapters.feishu.worker.threading.Thread", Reader):
            await _serve(Connection(), sdk)
        rejected = [packet for packet in sent if packet.get("error") == "Feishu worker capacity reached"]
        self.assertEqual(len(rejected), 22)
        self.assertLessEqual(sdk.send_component.await_count, 128)
