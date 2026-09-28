"""Account shutdown cancels in-flight sends without persistence tasks."""
import asyncio
import unittest

from tests.integration import test_channel_message_tool as fixtures


class ChannelOperationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_account_cancels_send_and_releases_tasks(self):
        fixture = fixtures.ChannelMessageIntegration()
        await fixture.asyncSetUp()
        self.addAsyncCleanup(fixture.engine.dispose)
        entered, cancelled = asyncio.Event(), asyncio.Event()

        async def blocked_send(*args, **kwargs):
            entered.set()
            try:
                await asyncio.Future()
            finally:
                cancelled.set()

        fixture.adapter.send_component.side_effect = blocked_send
        task = asyncio.create_task(fixture.operations.send(fixture.command(fixture.items()), fixture.items()))
        try:
            await asyncio.wait_for(entered.wait(), 2)
            await asyncio.wait_for(fixture.operations.close_account(fixture.account.id), 2)
            self.assertTrue(cancelled.is_set())
            self.assertTrue(task.cancelled())
            fixture.adapter.send_component.assert_awaited_once()
            self.assertFalse(fixture.operations.tasks)
            self.assertFalse(fixture.job.outbound_tasks)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
