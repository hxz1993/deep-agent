"""Real gateway/SSE/event storage, with isolated member data and fake wire."""
from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from pydantic_ai.models.function import FunctionModel

from deep_agent.core.context import get_context
from deep_agent.db.models import WecomApplication, WecomUserProfile
from deep_agent.schemas.channel import ChannelAccountUpdate
from tests.integration import test_wecom_ingress as fixture
from tests.integration.helpers.chat import mock_request_disconnect
from tests.unit.test_wecom_adapter import frame


class WecomIdentityEventsTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.WecomIngressIntegrationTests.asyncSetUp
    asyncTearDown = fixture.WecomIngressIntegrationTests.asyncTearDown
    _create_ingress = fixture.WecomIngressIntegrationTests._create_ingress
    _history = fixture.WecomIngressIntegrationTests._history

    async def test_resolved_private_and_group_identity_reaches_model_and_persisted_user_events(self):
        account, service, adapter, handler, requests = await self._create_ingress(stream_interval=0)
        ctx = get_context()
        now = datetime.now(timezone.utc)
        async with ctx.db_session_factory.begin() as db:
            db.add(WecomApplication(id="fixture-app", name="身份应用", corp_id="isolated-corp",
                                    wecom_agent_id="test-agent", app_secret="test-only"))
            for raw, userid, name in (("sender-test", "FirstMember", "成员甲"), ("member-b", "SecondMember", "成员乙")):
                db.add(WecomUserProfile(id=raw, wecom_application_id="fixture-app", open_userid=raw,
                    userid=userid, userid_key=userid.casefold(), name=name,
                    profile_synced_at=now, expires_at=now + timedelta(days=7)))
        await ctx.stores.channel.update_account(account.id, ChannelAccountUpdate(settings={
            "wecom": {"user_details_enabled": True, "application_id": "fixture-app"},
        }))
        model_inputs = []

        async def answer(messages, _info):
            model_inputs.append(str(messages))
            yield "身份测试已完成"

        async def resolve(account, *, sender_id, peer_type, peer_id, session_id):
            profile = await ctx.stores.wecom.find_profile("fixture-app", sender_id)
            await ctx.stores.channel.refresh_wecom_titles(account.id, application_id="fixture-app",
                sender_id=sender_id, userid=profile.userid, name=profile.name, revision=1, session_id=session_id)
            return {"status": "resolved", "application_id": "fixture-app", "userid": profile.userid, "name": profile.name}

        async def send(index, peer_type, sender_id):
            handler.handle_frame(frame(payload={"content": f"独立身份消息 {index}"}, msgid=f"identity-{index}",
                chattype=peer_type, chatid="isolated-group", **{"from": {"userid": sender_id}}), adapter.client)
            await adapter.client.flush()
            async with asyncio.timeout(10):
                while any(peer.active or peer.pending or peer.worker for peer in service.peers.values()):
                    await asyncio.sleep(0.01)

        with patch.object(service.deep_agent, "resolve_wecom_user", side_effect=resolve), \
             patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(stream_function=answer)), \
             mock_request_disconnect():
            await send(1, "single", "sender-test")
            await send(2, "single", "sender-test")
            await send(3, "group", "sender-test")
            await send(4, "group", "member-b")
        self.assertEqual(len(requests), 4)
        self.assertEqual(len(model_inputs), 4)
        self.assertIn("FirstMember", model_inputs[0])
        self.assertIn("成员甲", model_inputs[0])
        self.assertIn("SecondMember", model_inputs[3])
        self.assertIn("成员乙", model_inputs[3])
        private = await self._history(requests[0]["session_id"])
        users = [row for row in private["items"] if row["kind"] == "user_message"]
        self.assertEqual([len(row.get("context_refs") or []) for row in users], [1, 0])
        source = users[0]["context_refs"][0]["payload"]
        self.assertEqual({key: source[key] for key in ("sender_id", "peer_id", "userid", "name")},
            {"sender_id": "sender-test", "peer_id": "sender-test", "userid": "FirstMember", "name": "成员甲"})
        group = await self._history(requests[2]["session_id"])
        sources = [row["context_refs"][0]["payload"] for row in group["items"] if row["kind"] == "user_message"]
        self.assertEqual([row["sender_id"] for row in sources], ["sender-test", "member-b"])
        self.assertEqual([row["userid"] for row in sources], ["FirstMember", "SecondMember"])
        self.assertEqual([row["peer_id"] for row in sources], ["isolated-group", "isolated-group"])
        events = await ctx.stores.coordinator.list_events(requests[0]["session_id"], event_type="session/title")
        self.assertEqual(events[-1].payload["title"], "User｜成员甲(sender-test)")
        events = await ctx.stores.coordinator.list_events(requests[2]["session_id"], event_type="session/title")
        self.assertEqual(events[-1].payload["title"], "Group｜isolated-group")
