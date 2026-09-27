"""Cancellation must not detach an operation's durable terminal receipt."""
import asyncio
import unittest
from uuid import NAMESPACE_URL, uuid5

from tests.integration import test_channel_message_tool as fixtures


class ChannelOperationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.fixture = fixtures.ChannelMessageIntegration()
        await self.fixture.asyncSetUp()
        self.addAsyncCleanup(self.fixture.engine.dispose)

    async def test_cancel_during_final_write_waits_for_completed_receipt(self):
        fixture = self.fixture
        entered, release = asyncio.Event(), asyncio.Event()
        original = fixture.store.update_operation

        async def pause_final(*args, **kwargs):
            if kwargs["state"] == "completed":
                entered.set()
                await release.wait()
            return await original(*args, **kwargs)

        fixture.store.update_operation = pause_final
        task = asyncio.create_task(fixture.operations.send(fixture.command(fixture.items()), fixture.items()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            self.assertIn(task, fixture.operations.tasks)
            self.assertIn(task, fixture.job.outbound_tasks)
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()  # account shutdown and Run cancellation may overlap
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
            record = await fixture.store.get_operation(uuid5(NAMESPACE_URL, "deep-agent-channel:agent-run:tool-call").hex)
            self.assertEqual(record.state, "completed")
            self.assertEqual([item["state"] for item in record.results], ["succeeded"] * 3)
            self.assertFalse(fixture.operations.tasks)
            self.assertFalse(fixture.job.outbound_tasks)
        finally:
            release.set()
            await asyncio.gather(task, return_exceptions=True)

    async def test_close_account_waits_for_unknown_receipt_after_send_cancel(self):
        fixture = self.fixture
        sending, writing, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original = fixture.store.update_operation

        async def blocked_send(*args, **kwargs):
            sending.set()
            await asyncio.Future()

        async def pause_terminal(*args, **kwargs):
            if kwargs["state"] == "unknown":
                writing.set()
                await release.wait()
            return await original(*args, **kwargs)

        fixture.adapter.send_component.side_effect = blocked_send
        fixture.store.update_operation = pause_terminal
        task = asyncio.create_task(fixture.operations.send(fixture.command(fixture.items()), fixture.items()))
        closing = None
        try:
            await asyncio.wait_for(sending.wait(), 2)
            closing = asyncio.create_task(fixture.operations.close_account(fixture.account.id))
            await asyncio.wait_for(writing.wait(), 2)
            self.assertFalse(closing.done())
            self.assertIn(task, fixture.operations.tasks)
            task.cancel()  # second cancellation must not orphan the DB task
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            await asyncio.wait_for(closing, 2)
            record = await fixture.store.get_operation(uuid5(NAMESPACE_URL, "deep-agent-channel:agent-run:tool-call").hex)
            self.assertEqual(record.state, "unknown")
            self.assertEqual([item["state"] for item in record.results], ["unknown", "cancelled", "cancelled"])
            self.assertFalse(fixture.operations.tasks)
            self.assertFalse(fixture.job.outbound_tasks)
        finally:
            release.set()
            task.cancel()
            await asyncio.gather(task, *([closing] if closing else []), return_exceptions=True)
