from __future__ import annotations

import unittest
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

from deep_agent.api.v1.chat import _current_channel_context
from deep_agent.channels.service import ChannelService
from deep_agent.schemas.channel import ChannelAccountCreate
from tests.unit import test_channel_gateway as fixtures
from tests.unit import test_channel_commands as commands


class FeishuSourcePersistenceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.ChannelGatewayTests.asyncSetUp
    asyncTearDown = fixtures.ChannelGatewayTests.asyncTearDown
    message = commands.ChannelCommandTests.message
    wait_idle = commands.ChannelCommandTests.wait_idle

    async def test_group_senders_share_session_but_keep_separate_durable_identity(self):
        account = await self.store.create_account(ChannelAccountCreate(
            name="飞书测试", channel_type="feishu", bot_id="cli_fixture", secret="fixture",
            settings={"feishu": {"user_details_enabled": True}},
            agent_id=self.account.agent_id, session_user_id=self.account.session_user_id, enabled=True,
        ))
        captured = []
        class Agent(commands.FakeAgent):
            async def stream_chat(self, *args, **kwargs):
                captured.append(kwargs.get("context_refs"))
                async for event in super().stream_chat(*args, **kwargs):
                    yield event
        agent, adapter = Agent(), commands.FakeAdapter()
        service = ChannelService(self.store, agent)
        self.addAsyncCleanup(service.close_account, account.id)
        messages = [replace(self.message("继续分析", identifier=f"message-{i}", peer="oc_group", peer_type="group", sender=f"ou_{i}"),
                            sender_name=f"用户{i}", tenant_id="tenant", thread_id="thread",
                            source_metadata={"lark_union_id": f"on_{i}"}) for i in (1, 2)]
        for message in messages:
            await service.receive(account, adapter, message)
            await self.wait_idle(service)
        # Redelivery neither executes nor creates another run.
        await service.receive(account, adapter, messages[0])
        await self.wait_idle(service)
        self.assertEqual(len(agent.calls), 2)
        self.assertEqual(agent.calls[0][0], agent.calls[1][0])
        for i, refs in enumerate(captured, start=1):
            source = next(ref["payload"] for ref in refs if ref["type"] == "channel.message_source")
            self.assertEqual(source["name"], f"用户{i}")
            self.assertEqual(source["sender_id"], f"ou_{i}")
            row = await self.store.get_message(account.id, f"message-{i}")
            self.assertEqual((row.sender_name, row.tenant_id, row.thread_id), (f"用户{i}", "tenant", "thread"))
            self.assertEqual(row.source_metadata, {"lark_union_id": f"on_{i}"})
            # API revalidates enrichment against persisted facts, not supplied labels.
            source["name"] = "伪造名称"
            body = SimpleNamespace(context_refs=[SimpleNamespace(type="channel.message_source", payload=source)])
            await _current_channel_context(SimpleNamespace(stores=SimpleNamespace(channel=self.store)), body, account)
            self.assertEqual(source["name"], f"用户{i}")

    async def test_unknown_or_mismatched_sender_cannot_inject_enrichment(self):
        account = SimpleNamespace(id="a", channel_type="feishu", enabled=True, agent_id="agent", session_user_id="user")
        channel = SimpleNamespace(get_account=AsyncMock(return_value=account),
                                  get_message=AsyncMock(return_value=SimpleNamespace(sender_id="other")))
        payload = {"message_id": "m", "sender_id": "attacker", "name": "admin", "userid": "admin", "tenant_id": "other", "thread_id": "other"}
        body = SimpleNamespace(context_refs=[SimpleNamespace(type="channel.message_source", payload=payload)])
        await _current_channel_context(SimpleNamespace(stores=SimpleNamespace(channel=channel)), body, account)
        self.assertEqual(payload, {"message_id": "m", "sender_id": "attacker"})
