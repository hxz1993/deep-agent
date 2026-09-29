"""Identity enrichment preserves raw source facts and user-owned titles."""
from __future__ import annotations

import asyncio
import unittest
from uuid import uuid4

from deep_agent.channels.adapters.wecom.messages import parse_message
from deep_agent.channels.gateway.deepagent_client import StreamEvent
from deep_agent.channels.service import ChannelService
from deep_agent.db.models import ChatSession, ChatSessionEvent, WecomApplication
from deep_agent.schemas.channel import ChannelAccountUpdate
from tests.unit import test_channel_gateway as gateway_fixture


class WecomIdentityChannelTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = gateway_fixture.ChannelGatewayTests.asyncSetUp
    asyncTearDown = gateway_fixture.ChannelGatewayTests.asyncTearDown

    async def enable(self, application_id="application", enabled=True):
        async with self.factory.begin() as db:
            if await db.get(WecomApplication, application_id) is None:
                db.add(WecomApplication(id=application_id, name=application_id,
                    corp_id="fixture-corp", wecom_agent_id=application_id,
                    app_secret="test-only", revision=1))
        self.account = await self.store.update_account(self.account.id, ChannelAccountUpdate(settings={
            "wecom": {"user_details_enabled": enabled, "application_id": application_id},
        }))

    async def bind(self, peer_id="open-member", peer_type="single"):
        return await self.store.resolve_binding(account_id=self.account.id, peer_type=peer_type,
            peer_id=peer_id, peer_metadata={"display_name": "回调名称"})

    async def title(self, session_id):
        async with self.factory() as db:
            return (await db.get(ChatSession, session_id)).title

    async def test_first_lookup_persists_name_before_new_session_title(self):
        await self.enable()
        async def lookup():
            await self.stores.wecom.upsert_identity("application", 1, "Member", open_userid="open-member")
            await self.stores.wecom.save_profile("application", 1, "Member", "新成员")
            return {"application_id": "application", "application_revision": 1,
                    "userid": "Member", "name": "新成员"}
        calls = await self._send(lookup=lookup)
        session_id = next(value["session_id"] for kind, value in calls if kind == "model")
        self.assertEqual(await self.title(session_id), "wecom｜User｜新成员(open-member)")
        self.assertIsNone(next(value["session_id"] for kind, value in calls if kind == "lookup"))

    async def test_profile_settings_do_not_rewrite_existing_title_or_events(self):
        await self.enable()
        bound = await self.bind()
        sid = bound.session.session_id
        original = await self.title(sid)
        before = await self.stores.coordinator.list_events(sid, event_type="session/title")
        await self.enable(enabled=False)
        await self.enable(application_id="different-application")
        self.assertEqual(await self.title(sid), original)
        after = await self.stores.coordinator.list_events(sid, event_type="session/title")
        self.assertEqual([e.payload for e in before], [e.payload for e in after])

    async def _send(self, *, sender="open-member", group=False, lookup=None, persisted=False):
        calls = []
        adapter = gateway_fixture.FakeWeComClient()
        test = self
        class Client:
            async def resolve_wecom_user(self, account, **kwargs):
                test.assertEqual(adapter.replies[0], ("正在处理…", False))
                calls.append(("lookup", kwargs))
                if lookup:
                    return await lookup()
                return {"status": "resolved", "application_id": "application", "userid": sender.upper(), "name": "姓名"}

            async def stream_chat(self, account, **kwargs):
                calls.append(("model", kwargs))
                if persisted:
                    async with test.factory.begin() as db:
                        session = await db.get(ChatSession, kwargs["session_id"])
                        db.add(ChatSessionEvent(event_id=uuid4().hex, session_id=session.session_id,
                            sequence=session.next_sequence, schema_version=1, event_type="user/message",
                            payload={"source": "isolated-fixture"}))
                        session.next_sequence += 1
                yield StreamEvent("final", {"status": "completed", "output": "完成"})

        service = ChannelService(self.store, Client())
        message = parse_message({"headers": {"req_id": uuid4().hex}, "body": {
            "msgtype": "text", "msgid": uuid4().hex, "chattype": "group" if group else "single",
            "chatid": "group-id", "from": {"userid": sender}, "text": {"content": "独立消息测试"},
        }})
        await service.receive(self.account, adapter, message)
        peer = service.peers[(self.account.id, message.peer_type, message.peer_id)]
        if peer.worker:
            await asyncio.wait_for(peer.worker, 3)
        return calls

    def source(self, calls):
        refs = next(payload for kind, payload in calls if kind == "model").get("context_refs", [])
        return next((item["payload"] for item in refs if item["type"] == "channel.message_source"), None)

    async def test_opt_in_context_preserves_ids_and_private_first_persisted_message_only(self):
        await self.enable()
        first = self.source(await self._send(persisted=True))
        self.assertEqual((first["sender_id"], first["peer_id"]), ("open-member", "open-member"))
        self.assertEqual((first["userid"], first["name"]), ("OPEN-MEMBER", "姓名"))
        self.assertIsNone(self.source(await self._send()))

    async def test_group_each_author_and_unresolved_source(self):
        await self.enable()
        for member in ("first", "second"):
            source = self.source(await self._send(sender=member, group=True))
            self.assertEqual((source["peer_id"], source["sender_id"], source["userid"]),
                             ("group-id", member, member.upper()))
        async def unresolved():
            return {"status": "pending", "application_id": "application"}
        source = self.source(await self._send(lookup=unresolved))
        self.assertIsNone(source["userid"])
        self.assertIsNone(source["name"])

    async def test_default_off_never_queries_and_disabled_mid_query_drops_enrichment(self):
        calls = await self._send()
        self.assertEqual([kind for kind, _ in calls], ["model"])
        self.assertNotIn("userid", self.source(calls))
        await self.enable()
        async def disable():
            await self.enable(enabled=False)
            return {"status": "resolved", "application_id": "application", "userid": "Member", "name": "迟到姓名"}
        self.assertNotIn("userid", self.source(await self._send(lookup=disable)))

    async def test_application_changed_mid_query_drops_old_name_and_query_failure_does_not_block(self):
        await self.enable()
        async def switch():
            await self.enable(application_id="different-application")
            return {"status": "resolved", "application_id": "application", "userid": "Member", "name": "旧应用姓名"}
        source = self.source(await self._send(lookup=switch))
        self.assertIsNone(source["name"])
        self.assertIsNone(source["userid"])
        async def fail():
            raise TimeoutError("isolated-timeout")
        source = self.source(await self._send(lookup=fail))
        self.assertIsNone(source["name"])
