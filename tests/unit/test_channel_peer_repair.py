from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import unittest

from pydantic_ai.messages import ModelRequest, UserPromptPart
from sqlalchemy import select

from deep_agent.db.models import ChannelAccount, ChannelMessageRun, ChannelSessionBinding, ChatSession, ChatSessionEvent
from maintenance.channel_origins import repair_wecom_peer_types
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class ChannelPeerRepairTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        self.store = self.stores.channel
        agent_id = await seed_default_agent(self.stores)
        timestamp = datetime(2026, 1, 1, tzinfo=timezone.utc)
        async with self.factory.begin() as db:
            for kind in ("wecom", "other"):
                db.add(ChannelAccount(
                    id=kind, name=kind, channel_type=kind, transport="websocket",
                    bot_id=f"fixture-{kind}", secret="test-only-placeholder",
                    agent_id=agent_id, session_user_id="fixture-user", enabled=True,
                ))
            await db.flush()
            for index, (kind, peer_type) in enumerate((("wecom", "userid"), ("wecom", "chatid"), ("other", "userid"))):
                session = ChatSession(
                    session_id=f"session-{index}", agent_id=agent_id, user_id="fixture-user",
                    next_sequence=1, created_at=timestamp, updated_at=timestamp,
                )
                db.add(session)
                await db.flush()
                binding = ChannelSessionBinding(
                    id=f"binding-{index}", account_id=kind, peer_type=peer_type,
                    peer_id=f"peer-{index}", peer_metadata={"display_name": f"name-{index}"},
                    session_id=session.session_id, created_at=timestamp, updated_at=timestamp,
                )
                db.add(binding)
                await db.flush()
                await self.stores.events._append_title(
                    db, session, title=f"原渠道标题-{index}", source="channel",
                    source_event_id=None, turn_id=None, run_id=None, now=timestamp,
                    details={"channel": {
                        "account_id": kind, "account_name": kind, "channel_type": kind,
                        "peer_type": peer_type, "peer_id": binding.peer_id,
                        "peer_display_name": binding.peer_metadata["display_name"],
                    }},
                )
                await self.stores.events._append_title(
                    db, session, title=f"手工标题-{index}", source="user",
                    source_event_id=None, turn_id=None, run_id=None, now=timestamp,
                )
                await self.stores.events._append_messages(
                    db, session, [ModelRequest(parts=[UserPromptPart(content="保留原始正文")])],
                    turn_id=f"turn-{index}", run_id=f"agent-run-{index}", now=timestamp,
                )
                db.add(ChannelMessageRun(
                    id=f"run-{index}", account_id=kind, binding_id=binding.id,
                    external_message_id=f"external-{index}", session_id=session.session_id,
                    payload_hash="a" * 64, agent_run_id=f"agent-run-{index}",
                    processing_status="completed", delivery_status="sent", attempt_count=1,
                    created_at=timestamp, finished_at=timestamp,
                ))

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def snapshot(self):
        result = {}
        async with self.factory() as db:
            for model in (ChannelSessionBinding, ChatSession, ChatSessionEvent, ChannelMessageRun):
                table = model.__table__
                rows = (await db.execute(select(table).order_by(*table.primary_key.columns))).mappings().all()
                result[model.__tablename__] = [dict(row) for row in rows]
        return result

    async def test_dry_run_does_not_change_any_rows(self):
        before = await self.snapshot()
        self.assertEqual(await repair_wecom_peer_types(self.store), {"bindings_changed": 2, "origins_changed": 2})
        self.assertEqual(await self.snapshot(), before)

    async def test_apply_preserves_ids_titles_bodies_runs_and_other_channels(self):
        before = await self.snapshot()
        expected = deepcopy(before)
        aliases = {"userid": "single", "chatid": "group"}
        for binding in expected["channel_session_bindings"]:
            if binding["account_id"] == "wecom":
                binding["peer_type"] = aliases[binding["peer_type"]]
        for event in expected["chat_session_events"]:
            origin = event["payload"].get("channel")
            if event["payload"].get("source") == "channel" and origin["channel_type"] == "wecom":
                origin["peer_type"] = aliases[origin["peer_type"]]
        self.assertEqual(await repair_wecom_peer_types(self.store, apply=True), {"bindings_changed": 2, "origins_changed": 2})
        self.assertEqual(await self.snapshot(), expected)
        self.assertEqual(await repair_wecom_peer_types(self.store, apply=True), {"bindings_changed": 0, "origins_changed": 0})
        self.assertEqual(await self.snapshot(), expected)

    async def test_conflict_rejects_whole_repair_without_merging_or_deleting(self):
        async with self.factory.begin() as db:
            db.add(ChannelSessionBinding(
                id="conflicting-binding", account_id="wecom", peer_type="single",
                peer_id="peer-0", session_id=None,
            ))
        before = await self.snapshot()
        for apply in (False, True):
            with self.assertRaisesRegex(ValueError, "conflicting bindings"):
                await repair_wecom_peer_types(self.store, apply=apply)
            self.assertEqual(await self.snapshot(), before)


if __name__ == "__main__":
    unittest.main()
