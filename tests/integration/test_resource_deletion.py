from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, insert, select

from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.db.models import ChatSession, ChatSessionEvent, ChannelMessageRun, ChannelSessionBinding
from deep_agent.schemas.channel import ChannelAccountCreate
from deep_agent.services.resource_deletion import delete_resource, DeleteConfirmation
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class ResourceDeletionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        self.agent_id = await seed_default_agent(self.stores)
        self.ctx = AppContext.for_testing(stores=self.stores, db_engine=self.engine, db_session_factory=self.factory)
        init_context(self.ctx)
        self.app = FastAPI()
        self.app.include_router(api_router)
        allow_console_admin(self.app)
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test", headers={"user": "owner"})

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.ctx.event_persister.aclose()
        reset_context()
        await self.engine.dispose()

    async def seed_sessions(self):
        async with self.factory.begin() as db:
            for sid, user in (("mine", "owner"), ("theirs", "other")):
                await db.execute(insert(ChatSession), {"session_id": sid, "agent_id": self.agent_id, "user_id": user})
                await db.execute(insert(ChatSessionEvent), {"event_id": f"event-{sid}", "session_id": sid,
                    "sequence": 1, "event_type": "user/message", "payload": {"parts": []}})

    async def count(self, model):
        async with self.factory() as db:
            return await db.scalar(select(func.count()).select_from(model))

    async def test_agent_requires_exact_owner_bound_confirmation_and_cascades_events(self):
        await self.seed_sessions()
        path = f"/api/v1/agents/{self.agent_id}"
        response = await self.client.delete(path)
        self.assertEqual(response.status_code, 409, response.text)
        detail = response.json()["detail"]
        self.assertEqual(detail["code"], "delete_confirmation_required")
        self.assertEqual(detail["impact"], {"session_count": 2, "own_session_count": 1,
            "other_session_count": 1, "binding_count": 0, "run_count": 0})
        self.assertEqual(await self.count(ChatSessionEvent), 2)
        # A token from a different admin cannot silently authorize a different impact split.
        wrong_user = await self.client.request("DELETE", path, headers={"user": "third"}, json={"confirmation_token": detail["confirmation_token"]})
        self.assertEqual(wrong_user.status_code, 409)
        async with self.factory.begin() as db:
            await db.execute(insert(ChatSession), {"session_id": "new", "agent_id": self.agent_id, "user_id": "other"})
        changed = await self.client.request("DELETE", path, json={"confirmation_token": detail["confirmation_token"]})
        self.assertEqual(changed.status_code, 409)
        self.assertEqual(changed.json()["detail"]["impact"]["session_count"], 3)
        confirmed = await self.client.request("DELETE", path, json={"confirmation_token": changed.json()["detail"]["confirmation_token"]})
        self.assertEqual(confirmed.status_code, 200, confirmed.text)
        self.assertIsNone(await self.stores.agent.get_by_id(self.agent_id))
        self.assertEqual(await self.count(ChatSession), 0)
        self.assertEqual(await self.count(ChatSessionEvent), 0)

    async def seed_channel(self):
        channel = await self.stores.channel.create_account(ChannelAccountCreate(name="fixture", bot_id="bot", secret="fixture", agent_id=self.agent_id, session_user_id="owner"))
        peer = dict(account_id=channel.id, peer_type="single", peer_id="fixture-peer")
        resolved = await self.stores.channel.resolve_binding(**peer)
        claim = await self.stores.channel.claim_message(account_id=channel.id, binding_id=resolved.binding.id,
            external_message_id="message", external_request_id=None, sender_id="fixture-peer",
            session_id=resolved.session.session_id, payload_hash="0" * 64)
        return channel, resolved, claim

    async def test_channel_removes_only_ledger_and_rejects_late_children(self):
        channel, resolved, claim = await self.seed_channel()
        agent_delete = await self.client.delete(f"/api/v1/agents/{self.agent_id}")
        self.assertEqual(agent_delete.status_code, 409)
        self.assertIn("仅停用渠道不会解除关联", agent_delete.json()["detail"])
        path = f"/api/v1/channel-accounts/{channel.id}"
        preview = await self.client.delete(path)
        self.assertEqual(preview.status_code, 409, preview.text)
        detail = preview.json()["detail"]
        self.assertEqual(detail["impact"]["binding_count"], 1)
        self.assertEqual(detail["impact"]["run_count"], 1)
        done = await self.client.request("DELETE", path, json={"confirmation_token": detail["confirmation_token"]})
        self.assertEqual(done.status_code, 200, done.text)
        self.assertEqual(await self.count(ChannelSessionBinding), 0)
        self.assertEqual(await self.count(ChannelMessageRun), 0)
        self.assertEqual(await self.count(ChatSession), 1)
        self.assertEqual(await self.count(ChatSessionEvent), 1)
        self.assertIsNotNone(await self.stores.agent.get_by_id(self.agent_id))
        self.assertIsNone(await self.stores.channel.update_run(claim.run.id, processing_status="completed"))
        with self.assertRaises((ValueError, LookupError)):
            await self.stores.channel.ensure_binding(account_id=channel.id, peer_type="single", peer_id="late")
        self.assertEqual(await self.count(ChannelSessionBinding), 0)

    async def test_delete_waits_for_real_run_cleanup_then_removes_late_persisted_history(self):
        await self.seed_sessions()
        impact = await self.stores.agent.deletion_impact(self.agent_id, user_id="owner")
        entry = self.ctx.active_runs.register(agent_id=self.agent_id, session_id="mine", user_id="owner")
        async def finish_run():
            while not entry.cancellation_token.cancelled:
                await asyncio.sleep(.001)
            self.assertIsNotNone(await self.stores.agent.get_by_id(self.agent_id))
            async with self.factory.begin() as db:
                await db.execute(insert(ChatSessionEvent), {"event_id": "late", "session_id": "mine", "sequence": 2,
                    "event_type": "assistant/message", "payload": {"parts": []}})
            self.ctx.active_runs.unregister(entry)
        task = asyncio.create_task(finish_run())
        try:
            await delete_resource(self.ctx, self.stores.agent, self.agent_id, "owner", DeleteConfirmation(confirmation_token=impact.token))
            await task
        finally:
            if not task.done():
                task.cancel()
        self.assertEqual(await self.count(ChatSessionEvent), 0)
        self.assertTrue(entry.cancellation_token.cancelled)

    async def test_regular_session_delete_stops_run_and_keeps_agent_and_other_users(self):
        await self.seed_sessions()
        entry = self.ctx.active_runs.register(agent_id=self.agent_id, session_id="mine", user_id="owner")
        async def stop():
            while not entry.cancellation_token.cancelled:
                await asyncio.sleep(.001)
            self.ctx.active_runs.unregister(entry)
        task = asyncio.create_task(stop())
        response = await self.client.delete(f"/api/v1/sessions/mine?agent_id={self.agent_id}")
        await task
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.count(ChatSession), 1)
        forbidden = await self.client.delete(f"/api/v1/sessions/theirs?agent_id={self.agent_id}")
        self.assertEqual(forbidden.status_code, 404)
        self.assertIsNotNone(await self.stores.agent.get_by_id(self.agent_id))

    async def test_admin_guard_not_bypassed_by_confirmation(self):
        from deep_agent.security import ConsoleAdminAuthorizer
        self.app.dependency_overrides.clear()
        await self.seed_sessions()
        impact = await self.stores.agent.deletion_impact(self.agent_id, user_id="owner")
        with patch.object(ConsoleAdminAuthorizer, "access_for", AsyncMock(side_effect=HTTPException(status_code=403, detail="Forbidden"))):
            response = await self.client.request("DELETE", f"/api/v1/agents/{self.agent_id}", json={"confirmation_token": impact.token})
        self.assertIn(response.status_code, (401, 403))
        self.assertEqual(await self.count(ChatSession), 2)

    async def test_real_stream_is_stopped_before_agent_and_history_are_deleted(self):
        from tests.helpers.models import build_slow_stream_model
        from tests.integration.helpers.chat import mock_request_disconnect
        model, tracker = build_slow_stream_model(chunks=["chunk"] * 100, delay_s=.05)
        with patch("deep_agent.agent.factory._resolve_model", return_value=model), \
             patch("deep_agent.agent.runner.AgentRunner._generate_model_title", AsyncMock()), mock_request_disconnect():
            streaming = asyncio.create_task(self.client.post("/api/v1/chat/stream", json={
                "agent_id": self.agent_id, "session_id": "live", "message": "hello", "capabilities": []}))
            try:
                async with asyncio.timeout(5):
                    while tracker.yielded_chunks < 1:
                        await asyncio.sleep(.01)
                preview = await self.client.delete(f"/api/v1/agents/{self.agent_id}")
                self.assertEqual(preview.status_code, 409, preview.text)
                response = await self.client.request("DELETE", f"/api/v1/agents/{self.agent_id}", json={
                    "confirmation_token": preview.json()["detail"]["confirmation_token"]})
                self.assertEqual(response.status_code, 200, response.text)
                await asyncio.wait_for(streaming, 3)
                self.assertLess(tracker.yielded_chunks, tracker.total_chunks)
                self.assertEqual(self.ctx.active_runs.active_count(), 0)
                self.assertEqual(await self.count(ChatSession), 0)
                self.assertEqual(await self.count(ChatSessionEvent), 0)
                await asyncio.sleep(.05)
                self.assertEqual(await self.count(ChatSession), 0)
            finally:
                if not streaming.done():
                    streaming.cancel()
                await asyncio.gather(streaming, return_exceptions=True)

    async def test_agent_deletion_rolls_back_all_children_if_parent_delete_fails(self):
        from sqlalchemy import event
        await self.seed_sessions()
        impact = await self.stores.agent.deletion_impact(self.agent_id, user_id="owner")
        def fail_agent_delete(conn, cursor, statement, parameters, context, executemany):
            if statement.startswith("DELETE FROM agents"):
                raise RuntimeError("test rollback")
        event.listen(self.engine.sync_engine, "before_cursor_execute", fail_agent_delete)
        try:
            with self.assertRaisesRegex(RuntimeError, "test rollback"):
                await self.stores.agent.delete(self.agent_id, user_id="owner", confirmation_token=impact.token)
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", fail_agent_delete)
        self.assertEqual(await self.count(ChatSession), 2)
        self.assertEqual(await self.count(ChatSessionEvent), 2)
        self.assertIsNotNone(await self.stores.agent.get_by_id(self.agent_id))

    async def test_non_streaming_call_participates_in_deletion_cancellation(self):
        from pydantic_ai.models.function import FunctionModel
        started = asyncio.Event()
        async def respond(messages, info):
            started.set()
            await asyncio.Event().wait()
        model = FunctionModel(function=respond)
        with patch("deep_agent.agent.factory._resolve_model", return_value=model), \
             patch("deep_agent.agent.runner.AgentRunner._generate_model_title", AsyncMock()):
            running = asyncio.create_task(self.client.post("/api/v1/chat", json={
                "agent_id": self.agent_id, "session_id": "sync-live", "message": "hello", "capabilities": []}))
            try:
                await asyncio.wait_for(started.wait(), 5)
                deleted = await self.client.delete(f"/api/v1/sessions/sync-live?agent_id={self.agent_id}")
                self.assertEqual(deleted.status_code, 200, deleted.text)
                response = await asyncio.wait_for(running, 3)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "cancelled")
                self.assertEqual(await self.count(ChatSession), 0)
                self.assertEqual(await self.count(ChatSessionEvent), 0)
            finally:
                if not running.done():
                    running.cancel()
                await asyncio.gather(running, return_exceptions=True)

    async def test_manual_compaction_is_cancelled_before_session_delete(self):
        from pydantic_ai.models.function import FunctionModel
        from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart
        from tests.integration.helpers.chat import mock_request_disconnect
        history = []
        for turn in range(3):
            history.extend([ModelRequest(parts=[UserPromptPart(content=f"问题{turn}" + "必须保留这项背景。" * 500)]),
                ModelResponse(parts=[TextPart(content=f"回答{turn}" + "确认当前方案。" * 500)])])
        await self.ctx.store.append("compacting", history, agent_id=self.agent_id, user_id="owner",
                                    turn_id="seed", run_id="seed")
        started = asyncio.Event()
        async def respond(messages, info):
            started.set()
            await asyncio.Event().wait()
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(function=respond)), mock_request_disconnect():
            compacting = asyncio.create_task(self.client.post(f"/api/v1/sessions/compacting/compact?agent_id={self.agent_id}", json={}))
            try:
                await asyncio.wait_for(started.wait(), 5)
                deleted = await self.client.delete(f"/api/v1/sessions/compacting?agent_id={self.agent_id}")
                self.assertEqual(deleted.status_code, 200, deleted.text)
                await asyncio.gather(compacting, return_exceptions=True)
                self.assertEqual(self.ctx.active_runs.active_count(), 0)
                self.assertEqual(await self.count(ChatSession), 0)
                self.assertEqual(await self.count(ChatSessionEvent), 0)
            finally:
                if not compacting.done():
                    compacting.cancel()
                await asyncio.gather(compacting, return_exceptions=True)

    async def test_past_persistence_failure_does_not_permanently_block_deletion(self):
        from deep_agent.db.session_scope import runtime_session_key
        await self.seed_sessions()
        self.ctx.event_persister._write_errors[runtime_session_key("mine", agent_id=self.agent_id)] = "OldWriteFailure"
        response = await self.client.delete(f"/api/v1/sessions/mine?agent_id={self.agent_id}")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(await self.count(ChatSession), 1)

    async def test_parent_delete_clears_old_binding_after_channel_changed_agent(self):
        from deep_agent.schemas.agent import AgentCreate
        from deep_agent.schemas.channel import ChannelAccountUpdate
        channel, resolved, claim = await self.seed_channel()
        old_agent = await self.stores.agent.get_by_id(self.agent_id)
        other = await self.stores.agent.create_unique(AgentCreate(name="other-agent", model_id=old_agent.model_id, prompt="fixture"))
        await self.stores.channel.update_account(channel.id, ChannelAccountUpdate(agent_id=other.id))
        impact = await self.stores.agent.deletion_impact(self.agent_id, user_id="owner")
        self.assertTrue(await self.stores.agent.delete(self.agent_id, user_id="owner", confirmation_token=impact.token))
        binding = await self.stores.channel.get_binding(channel.id, "single", "fixture-peer")
        self.assertIsNone(binding.session_id)
        self.assertIsNotNone(await self.stores.channel.get_account(channel.id))
        from deep_agent.db.reference_schema import assert_logical_references
        async with self.engine.connect() as conn:
            await conn.run_sync(assert_logical_references)
