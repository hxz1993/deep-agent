from __future__ import annotations

import asyncio
import multiprocessing
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from deep_agent.channels.adapters.feishu import FeishuAdapter
from deep_agent.channels.adapters.feishu.worker import _serve
from tests.unit.test_feishu_adapter import account, sdk_message
from deep_agent.channels.adapters.feishu.messages import parse_message


class WorkerDispatchTest(unittest.IsolatedAsyncioTestCase):
    async def test_worker_routes_commands_and_waits_parent_admission(self):
        parent, child = multiprocessing.get_context("spawn").Pipe()
        adapter = SimpleNamespace(
            status=lambda: {"state": "connected"}, stop=AsyncMock(),
            test_connection=AsyncMock(return_value={"success": True}),
            download_media=AsyncMock(return_value=(b"data", "file")),
            reply_loading=AsyncMock(), reply=AsyncMock(side_effect=RuntimeError("private secret")),
        )
        callback = None
        async def start(handler):
            nonlocal callback
            callback = handler
        adapter.start = start
        task = asyncio.create_task(_serve(child, adapter))
        async def request(mid, method, *args):
            await asyncio.to_thread(parent.send, {"kind": "call", "id": mid, "method": method, "args": args})
            while True:
                packet = await asyncio.to_thread(parent.recv)
                if packet.get("kind") != "status":
                    return packet
        try:
            self.assertEqual((await request("start", "start"))["result"]["state"], "connected")
            pending = asyncio.create_task(callback(parse_message(sdk_message())))
            event = await asyncio.to_thread(parent.recv)
            self.assertEqual(event["kind"], "message")
            self.assertFalse(pending.done())
            await asyncio.to_thread(parent.send, {"kind": "admitted", "id": event["id"]})
            await asyncio.wait_for(pending, 2)
            self.assertTrue((await request("probe", "test_connection", 1))["result"]["success"])
            self.assertEqual((await request("media", "download_media", "handle"))["result"], (b"data", "file"))
            await request("loading", "reply_loading", "message", "reply")
            self.assertEqual((await request("reply", "reply", "m", "r", "x", True))["error"], "Feishu SDK operation failed")
            self.assertEqual((await request("unknown", "invalid"))["error"], "Feishu SDK operation failed")
            await request("stop", "stop")
            await asyncio.wait_for(task, 3)
        finally:
            parent.close(); child.close()
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)

    async def test_parent_pipe_eof_stops_adapter(self):
        parent, child = multiprocessing.get_context("spawn").Pipe()
        adapter = SimpleNamespace(status=lambda: {}, stop=AsyncMock())
        task = asyncio.create_task(_serve(child, adapter))
        await asyncio.to_thread(parent.recv)  # Wait until status publisher is active.
        parent.close()
        try:
            await asyncio.wait_for(task, 2)
            adapter.stop.assert_awaited_once()
        finally:
            child.close()


class ParentIPCTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.adapter = FeishuAdapter(account())

    def test_fingerprint_changes_policy_but_not_agent_binding(self):
        other = account()
        other.agent_id = "other"
        self.assertEqual(self.adapter.connection_fingerprint(self.adapter.account),
                         self.adapter.connection_fingerprint(other))
        self.assertNotEqual(self.adapter.connection_fingerprint(other),
                            self.adapter.connection_fingerprint(account(group_allowlist=["oc_group"])))

    async def test_dispatch_only_current_worker_results_and_errors(self):
        process = Mock()
        self.adapter._process = process
        future = asyncio.get_running_loop().create_future()
        self.adapter._pending["r"] = future
        self.adapter._dispatch({"kind": "result", "id": "r", "result": "wrong"}, Mock())
        self.assertFalse(future.done())
        self.adapter._dispatch({"kind": "result", "id": "r", "result": "okay"}, process)
        self.assertEqual(await future, "okay")
        other = asyncio.get_running_loop().create_future()
        self.adapter._pending["bad"] = other
        self.adapter._dispatch({"kind": "result", "id": "bad", "error": "sensitive"}, process)
        with self.assertRaisesRegex(RuntimeError, "^Feishu SDK operation failed$"):
            await other
        self.adapter._process = None

    async def test_disconnect_fails_outstanding_rpc_without_retrying_it(self):
        process = Mock()
        self.adapter._process = process
        future = asyncio.get_running_loop().create_future()
        self.adapter._pending["r"] = future
        self.adapter._lost(Mock())
        self.assertFalse(future.done())
        self.adapter._lost(process)
        with self.assertRaisesRegex(RuntimeError, "worker disconnected"):
            await future
        self.assertEqual(self.adapter.status()["state"], "failed")
        self.adapter._process = None

    async def test_rpc_timeout_releases_pending_slot_and_late_reply_is_ignored(self):
        self.adapter._send = Mock()
        with self.assertRaisesRegex(RuntimeError, "timed out"):
            await self.adapter._rpc("reply", timeout=0.01)
        self.assertFalse(self.adapter._pending)
        packet = self.adapter._send.call_args.args[0]
        self.adapter._dispatch({"kind": "result", "id": packet["id"], "result": None}, None)
        self.assertEqual(self.adapter._send.call_count, 1)

    async def test_rpc_send_failure_is_safe(self):
        self.adapter._send = Mock(side_effect=OSError("secret"))
        with self.assertRaisesRegex(RuntimeError, "^Feishu worker unavailable$"):
            await self.adapter._rpc("reply")
        self.assertFalse(self.adapter._pending)

    async def test_public_io_methods_preserve_arguments(self):
        self.adapter._rpc = AsyncMock(return_value=(b"file", "f"))
        self.assertEqual(await self.adapter.download_media("m"), (b"file", "f"))
        await self.adapter.reply_loading("m", "r", "loading")
        await self.adapter.reply("m", "r", "content", True)
        self.adapter._rpc.assert_awaited_with("reply", "m", "r", "content", True)
        self.assertNotEqual(self.adapter.new_reply_id(), self.adapter.new_reply_id())

    async def test_start_rpc_failure_reaps_worker(self):
        self.adapter._ensure_worker = AsyncMock()
        self.adapter._rpc = AsyncMock(side_effect=RuntimeError("failure"))
        self.adapter.stop = AsyncMock()
        with self.assertRaises(RuntimeError):
            await self.adapter.start(AsyncMock())
        self.adapter.stop.assert_awaited_once()

    async def test_stop_forcibly_kills_stuck_worker_and_clears_readers(self):
        process = Mock()
        process.is_alive.side_effect = [True, True, True, False]
        self.adapter._process = process
        self.adapter._connection = Mock()
        self.adapter._reader = Mock()
        self.adapter._rpc = AsyncMock(side_effect=TimeoutError())
        await self.adapter.stop()
        process.terminate.assert_called_once()
        process.kill.assert_called_once()
        self.assertIsNone(self.adapter._process)
        self.assertEqual(self.adapter.status()["state"], "closed")

    async def test_bounded_pending_and_receiver_capacity(self):
        self.adapter.max_pending = 0
        with self.assertRaisesRegex(RuntimeError, "capacity"):
            await self.adapter._rpc("anything")
        self.adapter._callback = AsyncMock()
        self.adapter._dispatch({"kind": "message", "id": "a"}, None)
        self.assertEqual(self.adapter.status()["state"], "failed")
        self.adapter._callback.assert_not_called()

    async def test_callback_failure_does_not_claim_successful_admission(self):
        self.adapter._callback = AsyncMock(side_effect=RuntimeError("private"))
        self.adapter._send = Mock()
        await self.adapter._receive({"message": "x", "id": "1"})
        self.adapter._send.assert_not_called()
        self.assertEqual(self.adapter.status()["state"], "failed")
