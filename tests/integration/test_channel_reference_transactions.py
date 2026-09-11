"""Opt-in real database channel reference races; never use shared databases.

Set DEEP_AGENT_CHANNEL_MYSQL_TEST_URL / DEEP_AGENT_CHANNEL_POSTGRES_TEST_URL.
Each URL must target the disposable database named ``channel_fk_test`` on a
loopback host. Tables are rebuilt for every test. No external delivery occurs.
"""
from __future__ import annotations

import asyncio
import os
import unittest

from sqlalchemy import delete, func, inspect, select, text
from alembic import command
from alembic.config import Config
from deep_agent.core.paths import REPO_ROOT
from sqlalchemy.engine import make_url

from deep_agent.db.dialect import resolve_dialect
from deep_agent.db.factory import create_engine
from deep_agent.db.models import (
    Agent, Base, ChannelAccount, ChannelMessageRun, ChannelSessionBinding,
    ChatSession, ChatSessionEvent,
)
from deep_agent.db.reference_integrity import ReferenceConflict, lock_reference
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.schemas.channel import ChannelAccountCreate, ChannelAccountUpdate
from tests.helpers.sqlite_stores import seed_default_agent, seed_default_llm_model


class ChannelReferenceTransactionScenarios:
    env_name: str

    async def asyncSetUp(self):
        url = os.environ[self.env_name]
        parsed = make_url(url)
        if parsed.database != "channel_fk_test" or parsed.host not in {"127.0.0.1", "localhost", "::1"}:
            raise AssertionError("Channel transaction tests require loopback channel_fk_test")
        self.engine, self.factory = create_engine(url)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            if await conn.run_sync(lambda db: inspect(db).has_table("alembic_version")):
                await conn.execute(text("DROP TABLE alembic_version"))
        def migrate():
            config = Config(str(REPO_ROOT / "alembic.ini"))
            config.attributes["database_url"] = url
            command.upgrade(config, "head")
        await asyncio.to_thread(migrate)
        async with self.engine.begin() as conn:
            constraints = await conn.run_sync(lambda db: [
                fk for table in Base.metadata.tables for fk in inspect(db).get_foreign_keys(table)
            ])
            self.assertEqual(constraints, [])
        self.stores = create_data_stores(resolve_dialect(url), self.factory)
        await seed_default_llm_model(self.stores)
        self.agent_a = await seed_default_agent(self.stores, name="agent-a")
        self.agent_b = await seed_default_agent(self.stores, name="agent-b")
        self.store = self.stores.channel
        self.account = await self.new_account("primary")
        self.peer = dict(account_id=self.account.id, peer_type="single", peer_id="fixture-peer")

    async def asyncTearDown(self):
        try:
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.drop_all)
                await conn.execute(text("DROP TABLE IF EXISTS alembic_version"))
        finally:
            await self.engine.dispose()

    async def new_account(self, bot):
        return await self.store.create_account(ChannelAccountCreate(
            name=bot, bot_id=bot, secret="test-adapter-placeholder", agent_id=self.agent_a,
            session_user_id="fixture-owner", enabled=True,
        ))

    async def claim(self, binding_id, message="fixture-message", **overrides):
        values = dict(account_id=self.account.id, binding_id=binding_id,
                      external_message_id=message, external_request_id=None,
                      sender_id="fixture-peer", session_id=None, payload_hash="a" * 64)
        values.update(overrides)
        return await self.store.claim_message(**values)

    async def assert_references_valid(self):
        async with self.factory() as db:
            for child, fk, parent, pk in [
                (ChannelAccount, ChannelAccount.agent_id, Agent, Agent.id),
                (ChatSession, ChatSession.agent_id, Agent, Agent.id),
                (ChannelSessionBinding, ChannelSessionBinding.account_id, ChannelAccount, ChannelAccount.id),
                (ChannelSessionBinding, ChannelSessionBinding.session_id, ChatSession, ChatSession.session_id),
                (ChannelMessageRun, ChannelMessageRun.account_id, ChannelAccount, ChannelAccount.id),
                (ChannelMessageRun, ChannelMessageRun.binding_id, ChannelSessionBinding, ChannelSessionBinding.id),
                (ChatSessionEvent, ChatSessionEvent.session_id, ChatSession, ChatSession.session_id),
            ]:
                count = await db.scalar(select(func.count()).select_from(child).outerjoin(parent, fk == pk)
                                        .where(fk.is_not(None), pk.is_(None)))
                self.assertEqual(count, 0, f"orphan {fk}")
            mismatch = await db.scalar(select(func.count()).select_from(ChannelMessageRun).join(
                ChannelSessionBinding, ChannelMessageRun.binding_id == ChannelSessionBinding.id,
            ).where(ChannelMessageRun.account_id != ChannelSessionBinding.account_id))
            self.assertEqual(mismatch, 0)

    async def concurrent(self, *operations):
        results = await asyncio.wait_for(asyncio.gather(*operations, return_exceptions=True), 15)
        for result in results:
            if isinstance(result, BaseException):
                self.assertIsInstance(result, (ValueError, LookupError), repr(result))
        await self.assert_references_valid()
        return results

    async def test_account_agent_switch_against_agent_delete(self):
        results = await self.concurrent(
            self.store.update_account(self.account.id, ChannelAccountUpdate(agent_id=self.agent_b)),
            self.stores.agent.delete(self.agent_b),
        )
        self.assertEqual(sum(isinstance(item, ReferenceConflict) for item in results), 1)
        account = await self.store.get_account(self.account.id)
        self.assertIsNotNone(await self.stores.agent.get_by_id(account.agent_id))

    async def test_parent_row_lock_blocks_writer_until_delete_commits(self):
        task = None
        try:
            async with self.factory.begin() as blocker:
                await lock_reference(blocker, Agent, Agent.id, self.agent_b, "Agent")
                task = asyncio.create_task(self.store.update_account(
                    self.account.id, ChannelAccountUpdate(agent_id=self.agent_b),
                ))
                await asyncio.sleep(0.15)
                self.assertFalse(task.done(), "writer bypassed the parent's database row lock")
                await blocker.execute(delete(Agent).where(Agent.id == self.agent_b))
            with self.assertRaisesRegex(ReferenceConflict, "Agent not found"):
                await asyncio.wait_for(task, 5)
        finally:
            if task is not None and not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual((await self.store.get_account(self.account.id)).agent_id, self.agent_a)
        await self.assert_references_valid()

    async def test_current_session_delete_races_same_agent_resolution(self):
        original = await self.store.resolve_binding(**self.peer)
        await self.concurrent(
            self.stores.coordinator.delete(original.session.session_id, agent_id=self.agent_a),
            self.store.resolve_binding(**self.peer),
        )
        resolved = await self.store.resolve_binding(**self.peer)
        self.assertNotEqual(original.session.session_id, resolved.session.session_id)
        self.assertEqual(resolved.session.agent_id, self.agent_a)
        await self.assert_references_valid()

    async def test_first_binding_against_empty_account_delete(self):
        results = await self.concurrent(self.store.ensure_binding(**self.peer), self.store.delete_account(self.account.id))
        account = await self.store.get_account(self.account.id)
        binding = await self.store.get_binding(**self.peer)
        self.assertEqual(account is None, binding is None)
        self.assertEqual(sum(isinstance(item, (ValueError, LookupError)) for item in results), 1)

    async def test_claim_and_delete_keep_existing_channel_history(self):
        binding = await self.store.ensure_binding(**self.peer)
        results = await asyncio.wait_for(asyncio.gather(
            self.claim(binding.id), self.store.delete_account(self.account.id), return_exceptions=True,
        ), 15)
        self.assertTrue(results[0].claimed)
        self.assertIsInstance(results[1], ValueError)
        self.assertIn("保留配置", str(results[1]))
        await self.assert_references_valid()

    async def test_old_agent_session_delete_races_new_agent_resolution(self):
        original = await self.store.resolve_binding(**self.peer)
        old_id = original.session.session_id
        await self.store.update_account(self.account.id, ChannelAccountUpdate(agent_id=self.agent_b))
        await self.concurrent(
            self.stores.coordinator.delete(old_id, agent_id=self.agent_a),
            self.store.resolve_binding(**self.peer),
        )
        resolved = await self.store.resolve_binding(**self.peer)
        self.assertNotEqual(resolved.session.session_id, old_id)
        self.assertEqual(resolved.session.agent_id, self.agent_b)
        async with self.factory() as db:
            self.assertIsNone(await db.get(ChatSession, old_id))
            self.assertEqual(await db.scalar(select(func.count()).select_from(ChatSessionEvent).where(
                ChatSessionEvent.session_id == old_id,
            )), 0)
        await self.assert_references_valid()

    async def test_duplicate_and_multiple_failed_retries(self):
        binding = await self.store.ensure_binding(**self.peer)
        claims = await self.concurrent(*(self.claim(binding.id) for _ in range(6)))
        self.assertEqual(sum(item.claimed for item in claims), 1)
        run = await self.store.get_message(self.account.id, "fixture-message")
        for attempt in range(2, 5):
            await self.store.update_run(run.id, processing_status="failed", error_code="preflight_failed")
            claims = await self.concurrent(*(self.claim(binding.id) for _ in range(4)))
            self.assertEqual(sum(item.claimed for item in claims), 1)
            run = await self.store.get_message(self.account.id, "fixture-message")
            self.assertEqual(run.attempt_count, attempt)
        await self.assert_references_valid()

    async def test_invalid_retry_does_not_mutate_run_and_weak_session_is_preserved(self):
        binding = await self.store.ensure_binding(**self.peer)
        run = (await self.claim(binding.id, session_id="weak-historical-session")).run
        await self.store.update_run(run.id, processing_status="failed", error_code="preflight_failed")
        with self.assertRaisesRegex(ReferenceConflict, "binding not found"):
            await self.claim("missing-binding")
        saved = await self.store.get_message(self.account.id, "fixture-message")
        self.assertEqual(saved.attempt_count, 1)
        self.assertEqual(saved.session_id, "weak-historical-session")
        self.assertEqual(saved.processing_status, "failed")
        await self.assert_references_valid()

    async def test_unicode_title_round_trip_with_new_session(self):
        resolved = await self.store.resolve_binding(**self.peer, peer_metadata={"display_name": "中文👋📊🙂"})
        again = await self.store.resolve_binding(**self.peer)
        self.assertEqual(again.session.session_id, resolved.session.session_id)
        self.assertIn("中文👋📊🙂", again.session.title)
        origin = await self.store.get_session_origin(again.session.session_id)
        self.assertEqual(origin["peer_metadata"]["display_name"], "中文👋📊🙂")
        await self.assert_references_valid()


@unittest.skipUnless(os.environ.get("DEEP_AGENT_CHANNEL_MYSQL_TEST_URL"), "isolated MySQL channel test URL not configured")
class MySqlChannelReferenceTransactions(ChannelReferenceTransactionScenarios, unittest.IsolatedAsyncioTestCase):
    env_name = "DEEP_AGENT_CHANNEL_MYSQL_TEST_URL"


@unittest.skipUnless(os.environ.get("DEEP_AGENT_CHANNEL_POSTGRES_TEST_URL"), "isolated PostgreSQL channel test URL not configured")
class PostgresChannelReferenceTransactions(ChannelReferenceTransactionScenarios, unittest.IsolatedAsyncioTestCase):
    env_name = "DEEP_AGENT_CHANNEL_POSTGRES_TEST_URL"
