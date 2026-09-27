"""Actual SDK frames and HTTP, using separate SDK processes and local servers."""
import asyncio
import multiprocessing
import os
import unittest
from types import SimpleNamespace

from deep_agent.channels.adapters.feishu import FeishuAdapter
from deep_agent.channels.adapters.feishu.messages import MediaHandle
from deep_agent.channels.contracts import InboundMedia
from tests.unit.feishu_adapter_fixtures import LocalFeishuPlatform, local_sdk_worker, orphan_gateway_entry, wait_connected


def adapter_for(platform, identifier, *, retry=False):
    return FeishuAdapter(SimpleNamespace(id=identifier, bot_id=identifier, secret="dummy",
        settings={"_test_domain": platform.domain}), infinite_retry=retry, _worker_target=local_sdk_worker)


class FeishuTransportTest(unittest.IsolatedAsyncioTestCase):
    async def test_real_sdk_frame_and_ack_before_durable_admission(self):
        async with LocalFeishuPlatform() as platform:
            adapter = adapter_for(platform, "cli_a")
            received, release = asyncio.Event(), asyncio.Event()
            delivered = []
            async def callback(message):
                received.set()
                await release.wait()
                delivered.append(message)
            try:
                await adapter.start(callback)
                await wait_connected(adapter)
                await platform.send("cli_a")
                async with asyncio.timeout(5):
                    await received.wait()
                    _app, ack = await platform.acks.get()
                    self.assertEqual(ack["code"], 200)
                    self.assertFalse(delivered)
                    release.set()
                    while not delivered:
                        await asyncio.sleep(0.01)
                self.assertEqual(delivered[0].tenant_id, "tenant_ws")
                self.assertEqual(delivered[0].text, "真实 SDK 消息")
                self.assertEqual(delivered[0].request_id, "evt_om_ws")
                media = InboundMedia(kind="file", filename="fixture.txt", mime="text/plain",
                    handle=MediaHandle("om_ws", "file_fixture", "file"))
                self.assertEqual(await adapter.download_media(media), (b"real-sdk-resource-bytes", "fixture.txt"))
                self.assertTrue(any("/messages/om_ws/resources/file_fixture?type=file" in path
                                    for path, _ in platform.http_calls))
                await adapter.reply(delivered[0], "r", "最终回复", True)
                self.assertTrue(any(path.endswith("/reply") for path, _ in platform.http_calls))
            finally:
                release.set()
                process = adapter._process
                await adapter.stop()
                self.assertFalse(process.is_alive())

    async def test_two_accounts_are_isolated_and_stopping_one_keeps_other_receiving(self):
        async with LocalFeishuPlatform() as platform:
            a, b = adapter_for(platform, "cli_a"), adapter_for(platform, "cli_b")
            received = asyncio.Queue()
            async def callback(message):
                received.put_nowait(message)
            try:
                await asyncio.gather(a.start(callback), b.start(callback))
                await asyncio.gather(wait_connected(a), wait_connected(b))
                self.assertNotEqual(a._process.pid, b._process.pid)
                await platform.send("cli_a", message_id="first")
                self.assertEqual((await asyncio.wait_for(received.get(), 5)).message_id, "first")
                await a.stop()
                self.assertTrue(b._process.is_alive())
                await platform.send("cli_b", message_id="after_a_stopped")
                self.assertEqual((await asyncio.wait_for(received.get(), 5)).message_id, "after_a_stopped")
            finally:
                await asyncio.gather(a.stop(), b.stop())

    async def test_worker_crash_recovers_transport_without_replaying_admitted_work(self):
        async with LocalFeishuPlatform() as platform:
            adapter = adapter_for(platform, "cli_restart", retry=True)
            received = asyncio.Queue()
            async def callback(message):
                received.put_nowait(message.message_id)
            try:
                await adapter.start(callback)
                await wait_connected(adapter)
                await platform.send("cli_restart", message_id="old")
                self.assertEqual(await asyncio.wait_for(received.get(), 5), "old")
                old = adapter._process
                old.terminate()
                async with asyncio.timeout(10):
                    while adapter._process is old or not adapter.status()["authenticated"]:
                        await asyncio.sleep(0.05)
                await platform.send("cli_restart", message_id="new")
                self.assertEqual(await asyncio.wait_for(received.get(), 5), "new")
                self.assertTrue(received.empty())
            finally:
                await adapter.stop()

    async def test_parent_process_exit_reclaims_sdk_worker(self):
        async with LocalFeishuPlatform() as platform:
            parent, child = multiprocessing.get_context("spawn").Pipe()
            process = multiprocessing.get_context("spawn").Process(target=orphan_gateway_entry, args=(child, platform.domain))
            process.start(); child.close()
            worker_pid = None
            try:
                async with asyncio.timeout(10):
                    while not parent.poll():
                        await asyncio.sleep(0.02)
                    worker_pid = parent.recv()
                process.terminate()
                await asyncio.to_thread(process.join, 3)
                async with asyncio.timeout(10):
                    while True:
                        try:
                            os.kill(worker_pid, 0)
                        except ProcessLookupError:
                            break
                        await asyncio.sleep(0.05)
            finally:
                parent.close()
                if process.is_alive():
                    process.kill()
                await asyncio.to_thread(process.join, 3)
                if worker_pid:
                    try:
                        os.kill(worker_pid, 9)
                    except ProcessLookupError:
                        pass

    async def test_connection_probe_uses_rest_and_reaps_temporary_worker(self):
        async with LocalFeishuPlatform() as platform:
            adapter = adapter_for(platform, "cli_probe")
            result = await adapter.test_connection(5)
            self.assertTrue(result["success"])
            self.assertIsNone(adapter._process)
            self.assertFalse(platform.connections)
            self.assertFalse(any(path == "/callback/ws/endpoint" for path, _ in platform.http_calls))
