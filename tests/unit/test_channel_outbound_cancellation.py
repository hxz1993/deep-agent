from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

from deep_agent.channels.contracts import InboundMessage
from deep_agent.channels.service import ChannelService, MessageJob, PeerQueue
from deep_agent.channels.gateway.deepagent_client import StreamEvent


class OutboundCancellationTests(unittest.IsolatedAsyncioTestCase):
    def make_job(self, message_id="inbound"):
        account = N(id="account", enabled=True, settings={}, channel_type="wecom", agent_id="agent", session_user_id="user")
        adapter = N(reply=AsyncMock(), reply_loading=AsyncMock())
        message = InboundMessage("text", message_id, None, "sender", "single", "sender", {}, "hello")
        return MessageJob(account, adapter, message, "run-" + message_id, "stream-" + message_id, 1,
                          session_id="session", agent_id="agent", dispatched=True)

    async def attach_blocked_upload(self, job):
        entered = asyncio.Event()
        cancelled = asyncio.Event()
        send = AsyncMock()
        async def upload_then_send():
            try:
                entered.set()
                await asyncio.Future()
                await send()
            finally:
                cancelled.set()
        task = asyncio.create_task(upload_then_send())
        job.outbound_tasks.add(task)
        task.add_done_callback(job.outbound_tasks.discard)
        await entered.wait()
        self.addAsyncCleanup(self.cleanup_task, task)
        return cancelled, send

    async def cleanup_task(self, task):
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_stop_cancels_upload_before_waiting_for_remote_api(self):
        active, command = self.make_job(), self.make_job("stop")
        cancelled, send = await self.attach_blocked_upload(active)
        store = N(update_run=AsyncMock(), get_binding=AsyncMock(return_value=N(session_id="session")))
        service = ChannelService(store, N())
        async def wait_remote(*args):
            self.assertTrue(active.stopped)
            self.assertTrue(cancelled.is_set(), "upload must stop before API cancellation round trip")
            send.assert_not_awaited()
            return True
        service._wait_stopped = wait_remote
        await service._control(PeerQueue(active=active), command, "/stop")
        self.assertTrue(cancelled.is_set())
        send.assert_not_awaited()

    async def test_terminal_cancels_upload_before_final_bubble_delivery(self):
        for event_name, event_data in [("final", {"status": "completed", "output": "done"}), ("cancelled", {}), ("error", {})]:
            with self.subTest(event=event_name):
                job = self.make_job()
                cancelled, send = await self.attach_blocked_upload(job)
                store = N(get_account=AsyncMock(return_value=job.account), update_run=AsyncMock(),
                          resolve_binding=AsyncMock(return_value=N(account=job.account, session=N(session_id="session", agent_id="agent"))),
                          has_user_messages=AsyncMock(return_value=True))
                async def stream(*args, **kwargs):
                    yield StreamEvent(event_name, event_data)
                service = ChannelService(store, N(stream_chat=stream))
                async def deliver(*args):
                    self.assertTrue(cancelled.is_set(), "upload must stop before terminal stream reply")
                    send.assert_not_awaited()
                service._deliver = deliver
                await service._process(job)
                self.assertTrue(job.terminal)
                send.assert_not_awaited()

    async def test_transport_error_and_task_cancel_stop_upload_before_failure_notice(self):
        for failure in (RuntimeError("stream failed"), asyncio.CancelledError()):
            with self.subTest(failure=type(failure).__name__):
                job = self.make_job()
                cancelled, send = await self.attach_blocked_upload(job)
                store = N(get_account=AsyncMock(return_value=job.account), update_run=AsyncMock(),
                          resolve_binding=AsyncMock(return_value=N(account=job.account, session=N(session_id="session", agent_id="agent"))),
                          has_user_messages=AsyncMock(return_value=True))
                async def stream(*args, **kwargs):
                    raise failure
                    yield
                service = ChannelService(store, N(stream_chat=stream))
                async def deliver(*args):
                    self.assertTrue(cancelled.is_set(), "failed run must stop uploads before any final network I/O")
                    send.assert_not_awaited()
                service._deliver = deliver
                if isinstance(failure, asyncio.CancelledError):
                    with self.assertRaises(asyncio.CancelledError):
                        await service._process(job)
                else:
                    await service._process(job)
                self.assertTrue(cancelled.is_set())


if __name__ == "__main__":
    unittest.main()
