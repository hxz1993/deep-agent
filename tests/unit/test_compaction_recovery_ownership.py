from __future__ import annotations

import asyncio
import unittest
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.db.models import ChatSessionEvent
from deep_agent.db.run_ownership import live_run, run_owner
from tests.unit import test_session_event_store as store_fixture


class CompactionRecoveryOwnershipTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await store_fixture.SessionEventStoreTests.asyncSetUp(self)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def compaction(self, *, session_id="session-1", run_id="run-1", status="running"):
        return await self.store.append_lifecycle(
            session_id, event_type="compaction", status=status,
            agent_id=self.agent_id, user_id="user-1", turn_id=run_id,
            run_id=run_id, payload={"trigger": "test", "source": {"through_sequence": 0}},
        )

    async def progress(self, *, session_id="session-1", run_id="run-1", foreign=True,
                       expired=False, status="running"):
        progress = await self.store.save_run_progress(
            session_id, agent_id=self.agent_id, user_id="user-1", turn_id=run_id,
            run_id=run_id, messages=[], revision=1,
        )
        async with self.factory() as db:
            row = await db.get(ChatSessionEvent, progress.event_id)
            owner = run_owner()
            if foreign:
                owner = {**owner, "host": "fixture-other-host-" + owner["host"]}
            row.payload = {
                **row.payload, "owner": owner,
                "lease_expires_at": (datetime.now(timezone.utc) + timedelta(
                    seconds=-120 if expired else 120,
                )).isoformat(),
            }
            row.status = status
            await db.commit()
        return progress

    async def snapshot(self, event):
        return next(row for row in await self.store.list_all_events(event.session_id)
                    if row.event_id == event.event_id)

    async def test_live_foreign_run_keeps_its_running_compaction_unchanged(self):
        compaction = await self.compaction()
        progress = await self.progress()
        compaction_before = (await self.snapshot(compaction)).public_dict()
        progress_before = (await self.snapshot(progress)).public_dict()
        self.assertEqual(await self.store.interrupt_running(), 0)
        self.assertEqual((await self.snapshot(compaction)).public_dict(), compaction_before)
        self.assertEqual((await self.snapshot(progress)).public_dict(), progress_before)
        self.assertEqual(await self.store.interrupt_running(), 0)

    async def test_expired_foreign_run_compaction_is_interrupted_once(self):
        compaction = await self.compaction()
        progress = await self.progress(expired=True)
        self.assertEqual(await self.store.interrupt_running(), 1)
        after = await self.snapshot(compaction)
        self.assertEqual((after.event_id, after.sequence, after.payload),
                         (compaction.event_id, compaction.sequence, compaction.payload))
        self.assertEqual(after.status, "interrupted")
        # Run recovery, not compaction cleanup, owns checkpoint finalization.
        self.assertEqual((await self.snapshot(progress)).status, "running")
        self.assertEqual(await self.store.interrupt_running(), 0)

    async def test_live_local_run_keeps_compaction_even_when_heartbeat_is_late(self):
        compaction = await self.compaction()
        await self.progress(foreign=False, expired=True)
        with live_run("run-1"):
            self.assertEqual(await self.store.interrupt_running(), 0)
        self.assertEqual((await self.snapshot(compaction)).status, "running")
        # An exited local Run no longer gets indefinite protection from its PID.
        self.assertEqual(await self.store.interrupt_running(), 1)

    async def test_local_terminal_commit_gap_is_protected_by_unexpired_lease(self):
        compaction = await self.compaction()
        await self.progress(foreign=False)
        self.assertEqual(await self.store.interrupt_running(), 0)
        self.assertEqual((await self.snapshot(compaction)).status, "running")

    async def test_manual_compactions_without_progress_keep_restart_semantics(self):
        with_run = await self.compaction(run_id="manual-run")
        without_run = await self.compaction(run_id=None)
        self.assertEqual(await self.store.interrupt_running(), 2)
        self.assertEqual((await self.snapshot(with_run)).status, "interrupted")
        self.assertEqual((await self.snapshot(without_run)).status, "interrupted")

    async def test_other_session_or_other_run_checkpoint_does_not_protect_compaction(self):
        compaction = await self.compaction()
        await self.progress(session_id="session-2")
        await self.progress(run_id="another-run")
        self.assertEqual(await self.store.interrupt_running(), 1)
        self.assertEqual((await self.snapshot(compaction)).status, "interrupted")

    async def test_terminal_checkpoints_do_not_protect_a_running_compaction(self):
        for status in ["completed", "pending", "failed", "cancelled", "interrupted"]:
            with self.subTest(status=status):
                run_id = "run-" + status
                compaction = await self.compaction(run_id=run_id)
                await self.progress(run_id=run_id, status=status)
                self.assertEqual(await self.store.interrupt_running(), 1)
                self.assertEqual((await self.snapshot(compaction)).status, "interrupted")

    async def test_already_terminal_compactions_are_never_rewritten(self):
        events = [await self.compaction(run_id="run-" + status, status=status)
                  for status in ["completed", "failed", "cancelled", "interrupted", "timed_out", "stale"]]
        before = [(await self.snapshot(event)).public_dict() for event in events]
        self.assertEqual(await self.store.interrupt_running(), 0)
        self.assertEqual([(await self.snapshot(event)).public_dict() for event in events], before)

    async def test_heartbeat_between_candidate_scan_and_lock_uses_fresh_lease(self):
        compaction = await self.compaction()
        await self.progress(foreign=False, expired=True)
        original_write = self.store.events._write

        @asynccontextmanager
        async def heartbeat_before_write():
            self.assertTrue(await self.store.touch_run_progress("session-1", run_id="run-1"))
            async with original_write() as db:
                yield db

        with patch.object(self.store.events, "_write", heartbeat_before_write):
            self.assertEqual(await self.store.interrupt_running(), 0)
        self.assertEqual((await self.snapshot(compaction)).status, "running")

    async def test_compaction_completed_after_scan_is_not_overwritten(self):
        compaction = await self.compaction()
        original_write = self.store.events._write
        advanced = False

        @asynccontextmanager
        async def completed_before_write():
            nonlocal advanced
            if not advanced:
                advanced = True
                await self.store.update_lifecycle(
                    compaction.event_id, status="completed", payload={"result": "kept"},
                )
            async with original_write() as db:
                yield db

        with patch.object(self.store.events, "_write", completed_before_write):
            self.assertEqual(await self.store.interrupt_running(), 0)
        after = await self.snapshot(compaction)
        self.assertEqual((after.status, after.payload), ("completed", {"result": "kept"}))

    async def test_overlapping_cleanup_only_counts_each_interruption_once(self):
        compaction = await self.compaction()
        await self.progress(expired=True)
        results = await asyncio.gather(self.store.interrupt_running(), self.store.interrupt_running())
        self.assertEqual(sum(results), 1)
        self.assertEqual((await self.snapshot(compaction)).status, "interrupted")

    async def test_session_and_progress_are_locked_before_compaction_update(self):
        await self.compaction()
        await self.progress(expired=True)
        statements = []
        original_execute = AsyncSession.execute

        async def record_execute(db, statement, *args, **kwargs):
            statements.append(str(statement.compile(dialect=postgresql.dialect())))
            return await original_execute(db, statement, *args, **kwargs)

        # SQLite exercises real Store transactions; compiling the very same
        # statements also checks the row-lock order used by PostgreSQL/MySQL.
        with patch.object(AsyncSession, "execute", record_execute):
            self.assertEqual(await self.store.interrupt_running(), 1)
        locked = [statement for statement in statements if "FOR UPDATE" in statement]
        self.assertEqual(len(locked), 2)
        self.assertIn("FROM chat_sessions", locked[0])
        self.assertIn("FROM chat_session_events", locked[1])
        update_index = next(i for i, sql in enumerate(statements) if sql.startswith("UPDATE chat_session_events"))
        self.assertGreater(update_index, statements.index(locked[1]))


if __name__ == "__main__":
    unittest.main()
