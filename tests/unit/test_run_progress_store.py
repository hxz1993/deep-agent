from __future__ import annotations

import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from pydantic_ai.messages import (
    ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart,
)

from deep_agent.db.run_progress import decode_run_progress_messages
from deep_agent.db.models import ChatSessionEvent
from deep_agent.db.run_ownership import LEASE_SECONDS, live_run, run_owner
from deep_agent.time import ensure_utc
from tests.unit import test_session_event_store as store_fixture


class RunProgressStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        await store_fixture.SessionEventStoreTests.asyncSetUp(self)
        self.identity = dict(agent_id=self.agent_id, user_id="user-1", turn_id="run-1", run_id="run-1")
        self.user = ModelRequest(parts=[UserPromptPart("question")], run_id="run-1")
        await self.store.append("session-1", [self.user], **self.identity)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def save(self, text="preview", *, revision=1, messages=None, **overrides):
        return await self.store.save_run_progress(
            "session-1", messages=messages if messages is not None else [ModelResponse(parts=[TextPart(text)])],
            revision=revision, **{**self.identity, **overrides},
        )

    async def progress(self):
        return next(row for row in await self.store.list_all_events("session-1") if row.event_type == "run/progress")

    async def set_lease(self, *, owner=None, expires_at=None, updated_at=None):
        """Simulate stale/foreign process records only inside this isolated fixture."""
        snapshot = await self.progress()
        async with self.factory() as db:
            row = await db.get(ChatSessionEvent, snapshot.event_id)
            payload = dict(row.payload)
            if owner is not None:
                payload["owner"] = owner
            if expires_at is not None:
                payload["lease_expires_at"] = expires_at.isoformat()
            row.payload = payload
            if updated_at is not None:
                row.updated_at = updated_at
            await db.commit()

    async def test_snapshot_is_durable_but_not_model_history(self):
        saved = await self.save()
        self.assertEqual(saved.sequence, 2)
        self.assertEqual(saved.status, "running")
        self.assertEqual(decode_run_progress_messages((await self.progress()).payload)[0].parts[0].content, "preview")
        self.assertEqual(await self.store.get_history("session-1", agent_id=self.agent_id), [self.user])
        self.assertEqual(await self.store.get_raw_history("session-1", agent_id=self.agent_id), [self.user])

    async def test_snapshot_can_commit_while_whole_run_lock_is_held(self):
        async with self.store.lock("session-1", agent_id=self.agent_id):
            row = await asyncio.wait_for(asyncio.create_task(self.save()), timeout=1)
        self.assertEqual(row.status, "running")

    async def test_revision_is_monotonic_and_sequence_stable(self):
        first = await self.save()
        updated = await self.save("latest", revision=3)
        self.assertIsNone(await self.save("stale", revision=2))
        self.assertIsNone(await self.save("duplicate", revision=3))
        self.assertEqual((first.event_id, first.sequence), (updated.event_id, updated.sequence))
        self.assertEqual((await self.progress()).payload["messages"][0]["parts"][0]["content"], "latest")
        self.assertEqual(len(await self.store.list_all_events("session-1")), 2)

    async def test_concurrent_snapshots_keep_highest_revision(self):
        await asyncio.gather(*(self.save(str(revision), revision=revision) for revision in [2, 6, 1, 4, 3, 5]))
        self.assertEqual((await self.progress()).payload["revision"], 6)
        self.assertEqual(len(await self.store.list_all_events("session-1")), 2)

    async def test_snapshot_does_not_retain_mutable_model_objects(self):
        response = ModelResponse(parts=[TextPart("original")])
        await self.save(messages=[response])
        response.parts[0].content = "mutated"
        self.assertEqual((await self.progress()).payload["messages"][0]["parts"][0]["content"], "original")

    async def test_snapshot_records_process_identity_and_bounded_lease(self):
        before = datetime.now(timezone.utc)
        saved = await self.save()
        after = datetime.now(timezone.utc)
        self.assertEqual(saved.payload["owner"], run_owner())
        expires = datetime.fromisoformat(saved.payload["lease_expires_at"])
        self.assertGreaterEqual(expires, before + timedelta(seconds=LEASE_SECONDS))
        self.assertLessEqual(expires, after + timedelta(seconds=LEASE_SECONDS))

    async def test_default_recovery_preserves_live_local_owner_even_with_expired_lease(self):
        await self.save()
        await self.set_lease(expires_at=datetime.now(timezone.utc) - timedelta(seconds=120))
        before = await self.progress()
        with live_run("run-1"):
            self.assertEqual(await self.store.recover_running_progress(), 0)
        after = await self.progress()
        self.assertEqual(after.public_dict(), before.public_dict())
        self.assertEqual(after.status, "running")
        self.assertEqual(await self.store.get_raw_history("session-1", agent_id=self.agent_id), [self.user])

    async def test_exited_same_process_run_recovers_after_commit_grace_expires(self):
        with live_run("run-1"):
            await self.save("completed work before failed terminal write")
        # Do not race a terminal transaction immediately after SDK exit.
        self.assertEqual(await self.store.recover_running_progress(), 0)
        await self.set_lease(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertEqual(await self.store.recover_running_progress(), 1)
        self.assertEqual((await self.progress()).status, "interrupted")
        history = await self.store.get_raw_history("session-1", agent_id=self.agent_id)
        self.assertEqual(history[-1].parts[0].content, "completed work before failed terminal write")
        self.assertEqual(await self.store.recover_running_progress(), 0)

    async def test_other_live_local_process_is_not_stolen_when_lease_expires(self):
        await self.save()
        await self.set_lease(owner={**run_owner(), "pid": os.getppid(), "boot_id": "another-process"},
                             expires_at=datetime.now(timezone.utc) - timedelta(seconds=120))
        self.assertEqual(await self.store.recover_running_progress(), 0)
        self.assertEqual((await self.progress()).status, "running")

    async def test_foreign_owner_is_recovered_only_after_lease_expiry(self):
        await self.save()
        owner = {**run_owner(), "host": "fixture-foreign-host-" + run_owner()["host"]}
        await self.set_lease(owner=owner, expires_at=datetime.now(timezone.utc) + timedelta(seconds=90))
        before = await self.progress()
        self.assertEqual(await self.store.recover_running_progress(), 0)
        self.assertEqual((await self.progress()).public_dict(), before.public_dict())
        await self.set_lease(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertEqual(await self.store.recover_running_progress(), 1)
        self.assertEqual((await self.progress()).status, "interrupted")
        self.assertEqual(await self.store.recover_running_progress(), 0)

    async def test_reused_current_pid_with_different_boot_identity_respects_lease(self):
        await self.save()
        await self.set_lease(owner={**run_owner(), "boot_id": "synthetic-old-process-boot"},
                             expires_at=datetime.now(timezone.utc) + timedelta(seconds=90))
        self.assertEqual(await self.store.recover_running_progress(), 0)
        await self.set_lease(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertEqual(await self.store.recover_running_progress(), 1)

    async def test_heartbeat_renews_lease_without_changing_snapshot_revision_or_sequence(self):
        await self.save("durable content", revision=4)
        old = datetime.now(timezone.utc) - timedelta(seconds=120)
        await self.set_lease(expires_at=old, updated_at=old)
        before = await self.progress()
        self.assertTrue(await self.store.touch_run_progress("session-1", run_id="run-1"))
        after = await self.progress()
        self.assertEqual((after.event_id, after.sequence, after.status), (before.event_id, before.sequence, "running"))
        self.assertEqual(after.payload["revision"], 4)
        self.assertEqual(after.payload["owner"], before.payload["owner"])
        self.assertEqual(after.payload["messages"], before.payload["messages"])
        self.assertGreater(ensure_utc(after.updated_at), ensure_utc(before.updated_at))
        self.assertGreater(datetime.fromisoformat(after.payload["lease_expires_at"]), datetime.now(timezone.utc))
        self.assertEqual(len(await self.store.list_all_events("session-1")), 2)

    async def test_heartbeat_cannot_renew_another_process_or_boot_identity(self):
        await self.save()
        current = run_owner()
        for key, value in [("host", "fixture-other-host"), ("pid", current["pid"] + 1), ("boot_id", "other-boot")]:
            with self.subTest(identity_field=key):
                await self.set_lease(owner={**current, key: value})
                before = await self.progress()
                self.assertFalse(await self.store.touch_run_progress("session-1", run_id="run-1"))
                self.assertEqual((await self.progress()).public_dict(), before.public_dict())

    async def test_heartbeat_ignores_missing_and_terminal_runs(self):
        self.assertFalse(await self.store.touch_run_progress("missing-session", run_id="run-1"))
        await self.save()
        self.assertFalse(await self.store.touch_run_progress("session-1", run_id="missing-run"))
        await self.store.append("session-1", [], terminal_status="completed", **self.identity)
        before = await self.progress()
        self.assertFalse(await self.store.touch_run_progress("session-1", run_id="run-1"))
        self.assertEqual((await self.progress()).public_dict(), before.public_dict())

    async def test_owner_and_identity_cannot_change(self):
        await self.save()
        with self.assertRaises(PermissionError):
            await self.save(revision=2, user_id="another-user")
        with self.assertRaises(PermissionError):
            await self.save(revision=2, agent_id="00000000-0000-4000-8000-000000000098")
        with self.assertRaisesRegex(ValueError, "turn identity"):
            await self.save(revision=2, turn_id="different-turn")
        with self.assertRaisesRegex(ValueError, "ordinal"):
            await self.save(revision=2, first_message_ordinal=2)
        self.assertEqual((await self.progress()).payload["revision"], 1)

    async def test_invalid_revision_or_ordinal_is_rejected_before_writes(self):
        for field in ["revision", "first_message_ordinal"]:
            for value in [-1, True, "1", 1.5]:
                with self.subTest(field=field, value=value), self.assertRaises(ValueError):
                    await self.save(**{field: value})
        self.assertEqual(len(await self.store.list_all_events("session-1")), 1)

    async def test_terminal_append_seals_progress_in_same_transaction(self):
        await self.save("long preliminary text")
        final = ModelResponse(parts=[TextPart("final")])
        rows = await self.store.append("session-1", [final], first_message_ordinal=1, terminal_status="completed", **self.identity)
        self.assertEqual([row.event_type for row in rows], ["assistant/message"])
        snapshot = await self.progress()
        self.assertEqual(snapshot.status, "completed")
        self.assertNotIn("messages", snapshot.payload)
        self.assertEqual(snapshot.payload["message_count"], 1)
        self.assertEqual(await self.store.get_raw_history("session-1", agent_id=self.agent_id), [self.user, final])
        self.assertIsNone(await self.save("late preview", revision=99))
        duplicate = await self.store.append("session-1", [final], first_message_ordinal=1, terminal_status="completed", **self.identity)
        self.assertEqual(rows[0].event_id, duplicate[0].event_id)
        self.assertEqual(len(await self.store.list_all_events("session-1")), 3)

    async def test_all_terminal_statuses_seal_even_with_no_messages(self):
        for index, status in enumerate(["completed", "pending", "interrupted", "cancelled", "failed"]):
            identity = {**self.identity, "run_id": f"run-{index + 10}", "turn_id": f"run-{index + 10}"}
            await self.store.save_run_progress("session-1", messages=[], revision=1, **identity)
            rows = await self.store.append("session-1", [], terminal_status=status, **identity)
            self.assertEqual(rows, [])
            self.assertIsNone(await self.store.save_run_progress("session-1", messages=[], revision=2, **identity))
            event = next(row for row in await self.store.list_all_events("session-1") if row.run_id == identity["run_id"])
            self.assertEqual(event.status, status)

    async def test_final_append_and_seal_rollback_together(self):
        await self.save()
        with patch.object(self.store.events, "_seal_run_progress", side_effect=RuntimeError("simulated commit failure")):
            with self.assertRaisesRegex(RuntimeError, "simulated"):
                await self.store.append("session-1", [ModelResponse(parts=[TextPart("final")])], first_message_ordinal=1, terminal_status="completed", **self.identity)
        self.assertEqual((await self.progress()).status, "running")
        self.assertEqual(await self.store.get_raw_history("session-1", agent_id=self.agent_id), [self.user])

    async def test_conflicting_terminal_messages_do_not_reopen_or_replace_progress(self):
        await self.save()
        await self.store.append("session-1", [ModelResponse(parts=[TextPart("final")])], first_message_ordinal=1, terminal_status="completed", **self.identity)
        with self.assertRaisesRegex(ValueError, "idempotency conflict"):
            await self.store.append("session-1", [ModelResponse(parts=[TextPart("changed")])], first_message_ordinal=1, terminal_status="failed", **self.identity)
        self.assertEqual((await self.progress()).status, "completed")

    async def test_late_first_snapshot_after_legacy_terminal_is_rejected(self):
        await self.store.append("session-1", [ModelResponse(parts=[TextPart("final")])], first_message_ordinal=1, terminal_status="completed", **self.identity)
        self.assertIsNone(await self.save(revision=99))
        self.assertEqual(len(await self.store.list_all_events("session-1")), 2)

    async def test_restart_recovers_committed_text_and_tool_result_without_replay(self):
        response = ModelResponse(parts=[TextPart("working"), ToolCallPart("read_fast", {}, "fast"), ToolCallPart("read_slow", {}, "slow")])
        request = ModelRequest(parts=[ToolReturnPart("read_fast", "done", "fast")], state="interrupted")
        await self.save(messages=[response, request])
        self.assertEqual(await self.store.recover_running_progress(force=True), 1)
        history = await self.store.get_raw_history("session-1", agent_id=self.agent_id)
        self.assertEqual(len(history), 3)
        self.assertEqual(history[1].state, "interrupted")
        self.assertEqual(history[2].parts[0].content, "done")
        self.assertEqual((await self.progress()).status, "interrupted")
        self.assertEqual((await self.progress()).payload["error"]["code"], "run_interrupted_by_restart")
        self.assertIsNone(await self.save("late", revision=99))
        self.assertEqual(await self.store.recover_running_progress(), 0)
        self.assertEqual(await self.store.get_raw_history("session-1", agent_id=self.agent_id), history)

    async def test_restart_before_any_response_creates_safe_terminal(self):
        await self.save(messages=[])
        self.assertEqual(await self.store.recover_running_progress(force=True), 1)
        events = await self.store.list_all_events("session-1")
        terminal = next(event for event in events if event.event_type == "run/terminal")
        self.assertEqual(terminal.status, "interrupted")
        self.assertEqual(terminal.payload, {"error": {"code": "run_interrupted_by_restart"}})
        self.assertEqual(await self.store.get_raw_history("session-1", agent_id=self.agent_id), [self.user])
        with self.assertRaisesRegex(ValueError, "terminal idempotency conflict"):
            await self.store.append("session-1", [ModelResponse(parts=[TextPart("late final")])], first_message_ordinal=1, terminal_status="completed", **self.identity)
        self.assertEqual(await self.store.get_raw_history("session-1", agent_id=self.agent_id), [self.user])

    async def test_restart_keeps_pending_deferred_and_compaction_untouched(self):
        pending = ModelResponse(parts=[ToolCallPart("builtin__confirm", {"question": "Confirm?"}, "pending-call")])
        await self.save(messages=[pending])
        await self.store.append("session-1", [pending], terminal_status="pending", first_message_ordinal=1, **self.identity)
        await self.store.append_lifecycle("session-1", event_type="compaction", status="running", payload={}, **self.identity)
        self.assertEqual(await self.store.recover_running_progress(), 0)
        self.assertEqual((await self.progress()).status, "pending")
        compaction = next(event for event in await self.store.list_all_events("session-1") if event.event_type == "compaction")
        self.assertEqual(compaction.status, "running")

    async def test_abandoned_recovery_closes_only_same_session_run_compaction(self):
        await self.save()
        current = await self.store.append_lifecycle(
            "session-1", event_type="compaction", status="running",
            payload={"phase": "summarizing"}, **self.identity,
        )
        unchanged = []
        for session_id, status, overrides in [
            ("session-1", "completed", {}),
            ("session-1", "running", {"run_id": "other-run", "turn_id": "other-run"}),
            ("session-1", "running", {"run_id": None, "turn_id": None}),
            ("session-2", "running", {}),
        ]:
            unchanged.append(await self.store.append_lifecycle(
                session_id, event_type="compaction", status=status,
                payload={"fixture": "preserve"}, **{**self.identity, **overrides},
            ))
        await self.set_lease(expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
        self.assertEqual(await self.store.recover_running_progress(), 1)
        all_events = {event.event_id: event for session in ("session-1", "session-2")
                      for event in await self.store.list_all_events(session)}
        self.assertEqual(all_events[current.event_id].status, "interrupted")
        self.assertEqual(all_events[current.event_id].payload, current.payload)
        self.assertEqual((await self.progress()).status, "interrupted")
        for event in unchanged:
            self.assertEqual(all_events[event.event_id].public_dict(), event.public_dict())
        self.assertEqual(await self.store.recover_running_progress(), 0)

    async def test_recovery_does_not_interrupt_live_run_compaction(self):
        await self.save()
        compaction = await self.store.append_lifecycle(
            "session-1", event_type="compaction", status="running", payload={}, **self.identity,
        )
        await self.set_lease(expires_at=datetime.now(timezone.utc) - timedelta(seconds=120))
        with live_run("run-1"):
            self.assertEqual(await self.store.recover_running_progress(), 0)
        current = next(event for event in await self.store.list_all_events("session-1")
                       if event.event_id == compaction.event_id)
        self.assertEqual(current.public_dict(), compaction.public_dict())

    async def test_recovery_does_not_change_completed_run_compaction(self):
        await self.save()
        compaction = await self.store.append_lifecycle(
            "session-1", event_type="compaction", status="running", payload={}, **self.identity,
        )
        await self.store.append("session-1", [ModelResponse(parts=[TextPart("final")])],
                                terminal_status="completed", first_message_ordinal=1, **self.identity)
        self.assertEqual(await self.store.recover_running_progress(), 0)
        current = next(event for event in await self.store.list_all_events("session-1")
                       if event.event_id == compaction.event_id)
        self.assertEqual(current.public_dict(), compaction.public_dict())

    async def test_failed_recovery_rolls_back_and_keeps_snapshot_for_retry(self):
        await self.save()
        compaction = await self.store.append_lifecycle(
            "session-1", event_type="compaction", status="running", payload={}, **self.identity,
        )
        with patch.object(self.store.events, "_seal_run_progress", side_effect=RuntimeError("simulated failure")):
            with self.assertRaises(RuntimeError):
                await self.store.recover_running_progress(force=True)
        self.assertEqual((await self.progress()).status, "running")
        self.assertEqual(await self.store.get_raw_history("session-1", agent_id=self.agent_id), [self.user])
        current = next(event for event in await self.store.list_all_events("session-1")
                       if event.event_id == compaction.event_id)
        self.assertEqual(current.public_dict(), compaction.public_dict())
        self.assertEqual(await self.store.recover_running_progress(force=True), 1)
        current = next(event for event in await self.store.list_all_events("session-1")
                       if event.event_id == compaction.event_id)
        self.assertEqual(current.status, "interrupted")

    async def test_exception_fallback_uses_durable_messages_and_seals_once(self):
        await self.save(messages=[ModelResponse(parts=[TextPart("partial"), ToolCallPart("read", {}, "call")])])
        result = await self.store.finish_run_progress("session-1", status="failed", **self.identity)
        self.assertEqual(result.status, "failed")
        history = await self.store.get_raw_history("session-1", agent_id=self.agent_id)
        self.assertEqual(history[1].parts[0].content, "partial")
        self.assertEqual(history[2].parts[0].outcome, "failed")
        self.assertIsNone(await self.store.finish_run_progress("session-1", status="interrupted", **self.identity))
        self.assertIsNone(await self.save("late", revision=100))

    async def test_exception_fallback_rejects_other_user_and_success(self):
        await self.save()
        with self.assertRaises(PermissionError):
            await self.store.finish_run_progress("session-1", status="interrupted", **{**self.identity, "user_id": "other"})
        with self.assertRaises(ValueError):
            await self.store.finish_run_progress("session-1", status="completed", **self.identity)
        self.assertEqual((await self.progress()).status, "running")


if __name__ == "__main__":
    unittest.main()
