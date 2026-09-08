from __future__ import annotations

import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, func, insert, select

from deep_agent.db.dialect import DbDialect
from deep_agent.db.factory import create_engine
from deep_agent.db.models import (
    Agent, Base, ChannelMessageRun, ChannelSessionBinding, ChatSession,
    ChatSessionEvent, LlmModel, LlmProvider,
)
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.schemas.channel import ChannelAccountCreate, ChannelAccountUpdate
from maintenance.channel_origins import repair_channel_origins


class ChannelStoreCommandTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TMP_to_delete-channel-store-")
        self.engine, self.factory = create_engine(
            f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'test.db'}"
        )
        async with self.engine.begin() as db:
            await db.run_sync(Base.metadata.create_all)
        self.agent_id = "00000000-0000-0000-0000-000000000001"
        async with self.factory.begin() as db:
            await db.execute(insert(LlmProvider), {
                "id": "1" * 32, "name": "fixture-provider", "provider_type": "openai",
                "use_default_base_url": True, "base_url": "https://example.test/v1",
                "api_key": "test-only-placeholder",
            })
            await db.execute(insert(LlmModel), {
                "id": self.agent_id, "provider_id": "1" * 32,
                "name": "fixture-model", "model": "openai:fixture", "enabled": True,
            })
            for number in (1, 2):
                await db.execute(insert(Agent), {
                    "id": f"00000000-0000-0000-0000-00000000000{number}",
                    "name": f"fixture-agent-{number}", "model_id": self.agent_id,
                    "capabilities": [], "skills": [], "prompt": "test",
                })
        self.stores = create_data_stores(DbDialect.SQLITE, self.factory)
        self.store = self.stores.channel
        self.account = await self.store.create_account(ChannelAccountCreate(
            name="测试渠道", bot_id="fixture-bot", secret="test-only-placeholder",
            agent_id=self.agent_id, session_user_id="platform-fixture", enabled=True,
        ))
        self.peer = dict(account_id=self.account.id, peer_type="single", peer_id="external-fixture")

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.tmp.cleanup()

    async def _claim(self, message_id="command-1", **peer):
        keys = peer or self.peer
        binding = await self.store.ensure_binding(**keys)
        return await self.store.claim_message(
            account_id=self.account.id, binding_id=binding.id,
            external_message_id=message_id, external_request_id="req-fixture",
            sender_id="external-fixture", session_id=binding.session_id, payload_hash="a" * 64,
        )

    async def _count(self, model):
        async with self.factory() as db:
            return (await db.execute(select(func.count()).select_from(model))).scalar_one()

    async def test_commands_can_be_claimed_without_creating_session(self):
        claimed = await self._claim()
        self.assertTrue(claimed.claimed)
        self.assertIsNone(claimed.run.session_id)
        self.assertEqual(await self._count(ChatSession), 0)
        self.assertEqual(await self._count(ChatSessionEvent), 0)
        self.assertFalse((await self._claim()).claimed)
        self.assertEqual(await self._count(ChannelMessageRun), 1)

    async def test_concurrent_first_bindings_and_messages_are_unique(self):
        rows = await asyncio.gather(*(self.store.ensure_binding(**self.peer) for _ in range(5)))
        self.assertEqual(len({row.id for row in rows}), 1)
        claims = await asyncio.gather(*(self._claim() for _ in range(5)))
        self.assertEqual(sum(result.claimed for result in claims), 1)
        self.assertEqual(await self._count(ChannelSessionBinding), 1)

    async def test_title_origin_blocks_fallback_and_model_but_allows_rename(self):
        resolved = await self.store.resolve_binding(**self.peer, peer_metadata={"display_name": "小明"})
        session_id = resolved.session.session_id
        self.assertEqual(resolved.session.title, "User｜小明（external-fixture）")
        self.assertEqual(resolved.session.next_sequence, 2)
        expected = dict(self.peer, account_name="测试渠道", channel_type="wecom", peer_metadata={"user_id": "external-fixture", "display_name": "小明"})
        self.assertEqual(await self.store.get_session_origin(session_id), expected)
        self.assertIsNone(await self.stores.coordinator.ensure_title_fallback(
            session_id, agent_id=self.agent_id, user_id="platform-fixture", title="不应替换",
            source_event_id="not-needed", turn_id=None, run_id=None,
        ))
        self.assertIsNone(await self.stores.coordinator.apply_session_title(
            session_id, agent_id=self.agent_id, user_id="platform-fixture", title="模型标题",
            source="model", source_event_id=None, turn_id=None, run_id=None,
        ))
        await self.stores.coordinator.apply_session_title(
            session_id, agent_id=self.agent_id, user_id="platform-fixture", title="手工标题",
            source="user", source_event_id=None, turn_id=None, run_id=None,
        )
        self.assertEqual(await self.store.get_session_origin(session_id), expected)

    async def test_new_atomically_rotates_and_duplicate_does_not_rotate_again(self):
        first = await self.store.resolve_binding(**self.peer)
        claim = await self._claim()
        rotated = await self.store.rotate_binding(**self.peer, command_run_id=claim.run.id)
        self.assertNotEqual(first.session.session_id, rotated.session.session_id)
        self.assertEqual(first.binding.id, rotated.binding.id)
        command = await self.store.get_message(self.account.id, "command-1")
        self.assertEqual(command.processing_status, "completed")
        self.assertEqual(command.delivery_status, "pending")
        self.assertEqual(command.session_id, rotated.session.session_id)
        repeated = await self.store.rotate_binding(**self.peer, command_run_id=claim.run.id)
        self.assertFalse(repeated.created_session)
        self.assertEqual(repeated.session.session_id, rotated.session.session_id)
        self.assertEqual(await self._count(ChatSession), 2)
        self.assertEqual(await self._count(ChannelSessionBinding), 1)
        self.assertEqual(len(await self.store.get_session_origins([
            first.session.session_id, rotated.session.session_id,
        ])), 2)

    async def test_rotation_rejects_wrong_peer_without_mutating(self):
        claim = await self._claim()
        other = {**self.peer, "peer_id": "another"}
        await self.store.ensure_binding(**other)
        with self.assertRaises(PermissionError):
            await self.store.rotate_binding(**other, command_run_id=claim.run.id)
        self.assertEqual(await self._count(ChatSession), 0)

    async def test_rotation_rejects_disabled_account_without_mutating(self):
        first = await self.store.resolve_binding(**self.peer)
        claim = await self._claim()
        await self.store.update_account(self.account.id, ChannelAccountUpdate(enabled=False))
        with self.assertRaisesRegex(PermissionError, "disabled"):
            await self.store.rotate_binding(**self.peer, command_run_id=claim.run.id)
        self.assertEqual(await self._count(ChatSession), 1)
        self.assertEqual((await self.store.get_binding(**self.peer)).session_id, first.session.session_id)
        self.assertEqual((await self.store.get_message(self.account.id, "command-1")).processing_status, "received")

    async def test_resolved_account_snapshot_matches_session_after_config_changes(self):
        first = await self.store.resolve_binding(**self.peer)
        self.assertEqual(first.account.session_user_id, first.session.user_id)
        await self.store.update_account(self.account.id, ChannelAccountUpdate(
            session_user_id="operator", agent_id="00000000-0000-0000-0000-000000000002",
        ))
        resolved = await self.store.resolve_binding(**self.peer)
        self.assertEqual(resolved.account.session_user_id, "operator")
        self.assertEqual(resolved.account.session_user_id, resolved.session.user_id)
        self.assertEqual(resolved.account.agent_id, resolved.session.agent_id)
        self.assertEqual(first.account.session_user_id, "platform-fixture")
        claim = await self._claim()
        rotated = await self.store.rotate_binding(**self.peer, command_run_id=claim.run.id)
        self.assertEqual(rotated.account.agent_id, rotated.session.agent_id)
        self.assertEqual(rotated.account.session_user_id, rotated.session.user_id)
        repeated = await self.store.rotate_binding(**self.peer, command_run_id=claim.run.id)
        self.assertEqual(repeated.account.id, self.account.id)

    async def test_resolution_reuses_and_changes_user_without_reassigning_history(self):
        first = await self.store.resolve_binding(**self.peer)
        repeated = await self.store.resolve_binding(**self.peer)
        self.assertEqual(repeated.binding.updated_at, first.binding.updated_at)
        self.assertEqual(repeated.session.session_id, first.session.session_id)
        claim = await self._claim()
        await self.store.update_run(claim.run.id, session_id=first.session.session_id)
        await self.store.update_account(self.account.id, ChannelAccountUpdate(session_user_id="operator"))
        next_session = await self.store.resolve_binding(**self.peer)
        historical = await self.store.get_channel_session(self.account.id, first.session.session_id)
        self.assertEqual(historical.user_id, "platform-fixture")
        self.assertEqual(next_session.session.user_id, "operator")
        self.assertIsNone(await self.store.get_channel_session("wrong-account", first.session.session_id))

    async def test_deleted_current_session_gets_recreated(self):
        first = await self.store.resolve_binding(**self.peer)
        async with self.factory.begin() as db:
            await db.execute(delete(ChatSession).where(ChatSession.session_id == first.session.session_id))
        second = await self.store.resolve_binding(**self.peer)
        self.assertNotEqual(first.session.session_id, second.session.session_id)
        self.assertEqual(first.binding.id, second.binding.id)

    async def test_restart_retries_only_undispatched_queue(self):
        for name, state in [("received", "received"), ("queued", "queued"), ("running", "running")]:
            claim = await self._claim(name)
            await self.store.update_run(claim.run.id, processing_status=state)
        self.assertEqual(await self.store.interrupt_incomplete(), 3)
        self.assertTrue((await self._claim("received")).claimed)
        self.assertTrue((await self._claim("queued")).claimed)
        self.assertFalse((await self._claim("running")).claimed)
        stopped = await self._claim("stopped")
        await self.store.update_run(stopped.run.id, processing_status="interrupted", error_code="user_stopped")
        self.assertFalse((await self._claim("stopped")).claimed)

    async def test_backfill_is_dry_run_idempotent_and_preserves_manual_titles(self):
        resolved = await self.store.resolve_binding(**self.peer)
        session_id = resolved.session.session_id
        async with self.factory.begin() as db:
            await db.execute(delete(ChatSessionEvent).where(ChatSessionEvent.session_id == session_id))
        await self.stores.coordinator.apply_session_title(
            session_id, agent_id=self.agent_id, user_id="platform-fixture", title="原人工标题",
            source="user", source_event_id=None, turn_id=None, run_id=None,
        )
        async with self.factory() as db:
            before = (await db.get(ChatSession, session_id)).updated_at
        self.assertEqual((await repair_channel_origins(self.store))["repaired"], 1)
        self.assertIsNone(await self.store.get_session_origin(session_id))
        self.assertEqual((await repair_channel_origins(self.store, apply=True))["repaired"], 1)
        self.assertEqual((await repair_channel_origins(self.store, apply=True))["already_present"], 1)
        async with self.factory() as db:
            after = await db.get(ChatSession, session_id)
            self.assertEqual(after.title, "原人工标题")
            self.assertEqual(after.updated_at, before)
        self.assertIsNotNone(await self.store.get_session_origin(session_id))

    async def test_account_summary_excludes_configuration_secrets(self):
        self.assertEqual(await self.store.account_summary(self.account.id), {
            "agent_name": "fixture-agent-1", "model_name": "fixture-model",
        })

    async def test_peer_metadata_updates_only_when_new_source_facts_arrive(self):
        first = await self.store.ensure_binding(**self.peer, peer_metadata={"user_id": "external-fixture"})
        same = await self.store.ensure_binding(**self.peer, peer_metadata={})
        self.assertEqual(first.updated_at, same.updated_at)
        changed = await self.store.ensure_binding(**self.peer, peer_metadata={
            "chat_id": "actual-private-chat", "display_name": "小明",
            "secret": "must-not-persist", "body": "must-not-persist",
        })
        self.assertEqual(changed.peer_metadata, {
            "user_id": "external-fixture", "chat_id": "actual-private-chat", "display_name": "小明",
        })
        empty = await self.store.ensure_binding(**self.peer, peer_metadata={"display_name": ""})
        self.assertEqual(empty.peer_metadata, changed.peer_metadata)
        self.assertEqual(empty.updated_at, changed.updated_at)
        group = await self.store.ensure_binding(
            account_id=self.account.id, peer_type="group", peer_id="actual-group",
            peer_metadata={"user_id": "message-sender", "chat_id": "wrong-id", "display_name": "业务群"},
        )
        self.assertEqual(group.peer_metadata, {"chat_id": "actual-group", "display_name": "业务群"})
        self.assertEqual(self.store.session_title(self.account, group), "Group｜业务群（actual-group）")
        private = await self.store.ensure_binding(**self.peer, peer_metadata={
            "user_id": "wrong-user", "chat_id": "actual-private-chat",
        })
        self.assertEqual(private.peer_metadata["user_id"], "external-fixture")
        self.assertEqual(private.peer_metadata["chat_id"], "actual-private-chat")

    async def test_channel_chat_skips_title_model_and_console_help_remains_chat(self):
        from fastapi import FastAPI
        from httpx import ASGITransport, AsyncClient
        from deep_agent.api.v1 import api_router
        from deep_agent.capabilities import build_registry
        from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
        from deep_agent.core.context import AppContext, init_context, reset_context
        from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
        from tests.helpers.models import text_only_model

        resolved = await self.store.resolve_binding(**self.peer)
        app = FastAPI()
        app.include_router(api_router)
        ctx = AppContext.for_testing(
            registry=build_registry(), stores=self.stores,
            mcp_lease_manager=McpLeaseManager(), db_engine=self.engine,
            db_session_factory=self.factory,
        )
        init_context(ctx)
        title_mock = AsyncMock()
        try:
            with patch("deep_agent.agent.factory._resolve_model", return_value=text_only_model("普通模型回复")), \
                    patch("deep_agent.agent.title_generator.generate_title", title_mock), mock_request_disconnect():
                async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
                    response = await client.post("/api/v1/chat/stream", headers={"user": "platform-fixture"}, json={
                        "agent_id": self.agent_id, "session_id": resolved.session.session_id,
                        "message": "/help", "capabilities": [],
                    })
                    self.assertEqual(response.status_code, 200, response.text)
                    events = parse_sse_events(response.text)
                    self.assertTrue(any(item["type"] == "text" for item in events))
                    self.assertFalse(any(item["type"] == "session_title" for item in events))
                    title_mock.assert_not_awaited()
        finally:
            await ctx.mcp_lease_manager.close_all()
            await ctx.event_persister.aclose()
            reset_context()

    async def test_backfill_uses_old_runs_after_binding_moves(self):
        first = await self.store.resolve_binding(**self.peer)
        first_claim = await self._claim("old-message")
        await self.store.update_run(first_claim.run.id, processing_status="completed")
        command = await self._claim("new-command")
        second = await self.store.rotate_binding(**self.peer, command_run_id=command.run.id)
        async with self.factory.begin() as db:
            await db.execute(delete(ChatSessionEvent).where(ChatSessionEvent.session_id == first.session.session_id))
            old = await db.get(ChatSession, first.session.session_id)
            await self.store._events._append_title(
                db, old, title="旧内容自动标题", source="fallback", source_event_id=None,
                turn_id=None, run_id=None,
            )
        self.assertEqual((await repair_channel_origins(self.store, apply=True))["repaired"], 1)
        self.assertIsNotNone(await self.store.get_session_origin(first.session.session_id))
        current = await self.store.get_binding(**self.peer)
        self.assertEqual(current.session_id, second.session.session_id)
        old = await self.store.get_channel_session(self.account.id, first.session.session_id)
        self.assertEqual(old.title, "User｜external-fixture")


if __name__ == "__main__":
    unittest.main()
