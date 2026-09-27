"""Real isolated SQLite persistence and migration checks; no platform sends."""
from __future__ import annotations

import asyncio
import importlib.util
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations

from deep_agent.core.paths import REPO_ROOT
from deep_agent.db.deletion import DeletionConfirmationRequired
from deep_agent.db.models import ChannelMessageOperation
from deep_agent.db.reference_integrity import ReferenceConflict
from deep_agent.schemas.channel import ChannelAccountCreate
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class OutboundReceiptTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        self.addAsyncCleanup(self.engine.dispose)
        self.agent_id = await seed_default_agent(self.stores)
        self.store = self.stores.channel
        self.account = await self.store.create_account(ChannelAccountCreate(
            name="fixture", bot_id="fixture-bot", secret="fixture-secret",
            agent_id=self.agent_id, session_user_id="owner",
        ))
        resolved = await self.store.resolve_binding(account_id=self.account.id, peer_type="single", peer_id="fixture-peer")
        self.binding = resolved.binding
        self.session = resolved.session
        claimed = await self.store.claim_message(
            account_id=self.account.id, binding_id=self.binding.id,
            external_message_id="fixture-message", external_request_id=None,
            sender_id="fixture-peer", session_id=self.session.session_id, payload_hash="a" * 64,
        )
        self.run = await self.store.update_run(claimed.run.id, agent_run_id="fixture-run", processing_status="running")
        self.args = dict(account_id=self.account.id, channel_run_id=self.run.id,
                         agent_run_id="fixture-run", tool_call_id="fixture-call",
                         session_id=self.session.session_id, request_hash="b" * 64,
                         results=[{"index": 0, "kind": "image", "state": "pending"}])

    async def test_concurrent_claim_has_one_owner_and_stable_receipt(self):
        rows = await asyncio.gather(*(self.store.claim_operation(**self.args) for _ in range(8)))
        self.assertEqual(sum(created for _, created in rows), 1)
        expected = uuid5(NAMESPACE_URL, "deep-agent-channel:fixture-run:fixture-call").hex
        self.assertEqual({row.id for row, _ in rows}, {expected})
        saved = [{"index": 0, "kind": "image", "state": "succeeded", "message_id": "msg"}]
        await self.store.update_operation(expected, state="completed", results=saved)
        retry, created = await self.store.claim_operation(**self.args)
        self.assertFalse(created)
        self.assertEqual(retry.state, "completed")
        self.assertEqual(retry.results, saved)
        self.assertEqual((await self.store.get_run(self.run.id)).id, self.run.id)
        self.assertEqual((await self.store.get_binding_by_id(self.binding.id)).id, self.binding.id)

    async def test_same_call_changed_payload_or_source_is_rejected(self):
        await self.store.claim_operation(**self.args)
        with self.assertRaisesRegex(ValueError, "idempotency conflict"):
            await self.store.claim_operation(**{**self.args, "request_hash": "c" * 64})
        for override in ({"account_id": "missing"}, {"session_id": "missing"}, {"agent_run_id": "another-run"}):
            with self.assertRaises(ReferenceConflict):
                await self.store.claim_operation(**{**self.args, **override})

    async def test_receipts_cannot_contain_message_bodies_or_bytes(self):
        for results in ([{"text": "secret body"}], [{"data": b"file bytes"}], [{"message_id": {"body": "secret"}}]):
            with self.assertRaises(ValueError):
                await self.store.claim_operation(**{**self.args, "results": results})
        row, _ = await self.store.claim_operation(**self.args)
        with self.assertRaises(ValueError):
            await self.store.update_operation(row.id, state="completed", results=[{"text": "body"}])
        self.assertEqual((await self.store.get_operation(row.id)).state, "pending")

    async def test_recovery_preserves_success_and_fences_late_writer(self):
        row, _ = await self.store.claim_operation(**self.args)
        saved = [{"index": 0, "kind": "image", "state": "succeeded", "message_id": "msg"},
                 {"index": 1, "kind": "file", "state": "running"},
                 {"index": 2, "kind": "text", "state": "pending"}]
        await self.store.update_operation(row.id, state="running", results=saved)
        complete, _ = await self.store.claim_operation(**{**self.args, "tool_call_id": "complete"})
        await self.store.update_operation(complete.id, state="completed", results=saved[:1])
        self.assertEqual(await self.store.recover_operations(), 1)
        self.assertEqual(await self.store.recover_operations(), 0)
        recovered = await self.store.get_operation(row.id)
        self.assertEqual(recovered.state, "unknown")
        self.assertEqual(recovered.results, [saved[0], {**saved[1], "state": "unknown"}, {**saved[2], "state": "unknown"}])
        late = await self.store.update_operation(row.id, state="completed", results=saved[:1])
        self.assertEqual(late.state, "unknown")
        self.assertEqual((await self.store.get_operation(complete.id)).state, "completed")

    async def test_channel_delete_cleans_receipts_after_confirmation(self):
        row, _ = await self.store.claim_operation(**self.args)
        with self.assertRaises(DeletionConfirmationRequired):
            await self.store.delete_account(self.account.id, user_id="owner")
        self.assertIsNotNone(await self.store.get_operation(row.id))
        impact = await self.store.deletion_impact(self.account.id, user_id="owner")
        await self.store.delete_account(self.account.id, user_id="owner", confirmation_token=impact.token)
        self.assertIsNone(await self.store.get_operation(row.id))
        self.assertIsNone(await self.store.get_run(self.run.id))
        self.assertTrue((await self.stores.coordinator.get_session_owner(self.session.session_id, agent_id=self.agent_id)).exists)

    async def test_session_delete_preserves_dedupe_receipt_but_rejects_new_claim(self):
        row, _ = await self.store.claim_operation(**self.args)
        await self.stores.coordinator.delete(self.session.session_id, agent_id=self.agent_id)
        self.assertIsNotNone(await self.store.get_operation(row.id))
        self.assertIsNone((await self.store.get_binding_by_id(self.binding.id)).session_id)
        with self.assertRaises(ReferenceConflict):
            await self.store.claim_operation(**{**self.args, "tool_call_id": "after-delete"})


class OutboundReceiptMigrationTests(unittest.TestCase):
    def test_008_009_roundtrip_and_nonempty_downgrade_guard(self):
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-channel-receipts-") as directory:
            path = Path(directory) / "test.db"
            config = Config(str(REPO_ROOT / "alembic.ini"))
            config.attributes["database_url"] = f"sqlite+aiosqlite:///{path}"
            command.upgrade(config, "008")
            with sqlite3.connect(path) as db:
                before = db.execute("SELECT id,name FROM agents").fetchall()
            command.upgrade(config, "009")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT id,name FROM agents").fetchall(), before)
                self.assertEqual(db.execute("PRAGMA foreign_key_list(channel_message_operations)").fetchall(), [])
                actual = {row[1] for row in db.execute("PRAGMA table_info(channel_message_operations)")}
                self.assertEqual(actual, set(ChannelMessageOperation.__table__.columns.keys()))
                db.execute("INSERT INTO channel_message_operations (id,account_id,channel_run_id,agent_run_id,tool_call_id,session_id,request_hash,results) VALUES ('op','account','channel-run','agent-run','call','session','hash','[]')")
            with self.assertRaisesRegex(RuntimeError, "Cannot downgrade"):
                command.downgrade(config, "008")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("009",))
                db.execute("DELETE FROM channel_message_operations")
            command.downgrade(config, "008")
            command.upgrade(config, "009")
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute("SELECT version_num FROM alembic_version").fetchone(), ("009",))
                self.assertEqual(db.execute("SELECT id,name FROM agents").fetchall(), before)

    def test_all_dialect_ddl_preserves_idempotency_constraint(self):
        spec = importlib.util.spec_from_file_location("outbound_migration", REPO_ROOT / "migrations/versions/009_channel_message_operations.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        for dialect in ("sqlite", "mysql", "postgresql"):
            output = io.StringIO()
            context = MigrationContext.configure(dialect_name=dialect, opts={"as_sql": True, "output_buffer": output})
            with Operations.context(context):
                module.upgrade()
            ddl = output.getvalue()
            self.assertIn("CREATE TABLE channel_message_operations", ddl)
            self.assertIn("uq_channel_operations_run_tool", ddl)
            self.assertNotIn("FOREIGN KEY", ddl)
