from __future__ import annotations

import unittest
from unittest.mock import patch

from tests.unit import test_channel_commands as command_fixtures
from tests.unit import test_channel_gateway as gateway_fixtures
from deep_agent.channels.service import ChannelService


class ChannelDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = gateway_fixtures.ChannelGatewayTests.asyncSetUp
    asyncTearDown = gateway_fixtures.ChannelGatewayTests.asyncTearDown
    message = command_fixtures.ChannelCommandTests.message
    wait_idle = command_fixtures.ChannelCommandTests.wait_idle

    async def test_receive_queue_dispatch_link_and_final_failure_are_distinct(self):
        rows = []
        agent, adapter = command_fixtures.FakeAgent(), command_fixtures.FakeAdapter()
        adapter.fail_delivery = True
        service = ChannelService(self.store, agent)
        try:
            with patch("deep_agent.diagnostics.emit", side_effect=lambda name, **fields: rows.append((name, fields))):
                await service.receive(self.account, adapter, self.message("private test body"))
                await self.wait_idle(service)
            names = [name for name, _ in rows]
            self.assertEqual(names, ["channel_received", "channel_queued", "channel_dispatched",
                                    "channel_agent_linked", "channel_completed", "channel_delivery_failed"])
            linked = next(fields for name, fields in rows if name == "channel_agent_linked")
            self.assertNotEqual(linked["channel_run_id"], linked["run_id"])
            self.assertTrue(linked["session_id"])
            self.assertEqual(rows[-1][1]["run_id"], linked["run_id"])
            self.assertEqual(len(agent.calls), 1)
            self.assertNotIn("private", repr(rows))
            self.assertNotIn("channel_delivery_sent", names)
        finally:
            await service.close_account(self.account.id)

    async def test_broken_diagnostic_sink_does_not_change_channel_reply_or_reexecute(self):
        agent, adapter = command_fixtures.FakeAgent(), command_fixtures.FakeAdapter()
        service = ChannelService(self.store, agent)
        try:
            with patch("deep_agent.diagnostics.emit", side_effect=RuntimeError("sink unavailable")):
                await service.receive(self.account, adapter, self.message("fixture"))
                await self.wait_idle(service)
            self.assertEqual(len(agent.calls), 1)
            self.assertEqual(adapter.replies[-1][1:], ("回答", True))
        finally:
            await service.close_account(self.account.id)
