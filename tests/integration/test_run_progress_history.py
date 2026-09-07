"""Running SDK work is observable through independent persisted History reads.

All data and tools are synthetic. A file-backed temporary SQLite ensures GET
requests see committed transactions, not the runner's in-memory message list.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai import Agent, FunctionToolset
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.run_progress import RunProgressCapability
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.registry import build_external_toolset
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.skills.runtime import SkillRuntimeLibrary
from tests.helpers.request_identity import TEST_USER_ID, install_test_user_header
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


def _call(name: str, call_id: str, args: dict | None = None) -> dict:
    return {0: DeltaToolCall(name=name, json_args=json.dumps(args or {}), tool_call_id=call_id)}


def _kind(history: dict, kind: str) -> list[dict]:
    return [item for item in history["items"] if item["kind"] == kind]


class RunProgressHistoryIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(
            prefix="TMP_to_delete-run-progress-history-", dir=Path(__file__).resolve().parents[2],
        )
        stores, engine, factory = await make_sqlite_stores(
            f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'history.sqlite3'}",
        )
        self.agent_id = await seed_default_agent(stores, name="isolated-progress-history")
        self.store = stores.coordinator
        self.ctx = AppContext.for_testing(
            registry=build_registry(), stores=stores, db_engine=engine,
            db_session_factory=factory,
            skill_runtime=SkillRuntimeLibrary(Path(self.tmp.name) / "skills"),
        )
        init_context(self.ctx)
        await self.ctx.event_persister.start()
        app = FastAPI()
        install_test_user_header(app)
        app.include_router(api_router)
        self.http = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        self.no_title = patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock)
        self.no_title.start()
        self.tasks: list[asyncio.Task] = []

    async def asyncTearDown(self) -> None:
        self.ctx.active_runs.cancel_all()
        for task in self.tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*self.tasks, return_exceptions=True)
        self.no_title.stop()
        await self.http.aclose()
        await self.ctx.aclose()
        reset_context()
        self.tmp.cleanup()

    def _runner(self, stream, *, tools=(), ui=False) -> AgentRunner:
        toolsets = [FunctionToolset(tools=list(tools))] if tools else []
        if ui:
            toolsets.append(build_external_toolset(builtin_ui_records()))
        return AgentRunner(
            Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps, toolsets=toolsets),
            self.store, UsageLimits(request_limit=8), active_runs=self.ctx.active_runs,
            ui_tool_hints={"builtin__confirm": {"widget": "confirm"}} if ui else None,
        )

    def _start(self, runner, session: str, *, on_event=None):
        events: list[dict] = []

        async def collect():
            deps = AgentDeps(session_id=session, user=TEST_USER_ID, agent_id=self.agent_id)
            async for event in runner.run_stream(
                session, "synthetic progress verification", deps, persister=self.ctx.event_persister,
            ):
                events.append(event)
                if on_event is not None:
                    await on_event(event)

        task = asyncio.create_task(collect())
        self.tasks.append(task)
        return task, events

    async def _ready(self, ready: asyncio.Event, task: asyncio.Task) -> None:
        waiter = asyncio.create_task(ready.wait())
        try:
            completed, _ = await asyncio.wait({waiter, task}, timeout=5, return_when=asyncio.FIRST_COMPLETED)
            if task in completed:
                await task
            self.assertTrue(ready.is_set(), "Run terminated or stalled before the deterministic barrier")
        finally:
            waiter.cancel()
            await asyncio.gather(waiter, return_exceptions=True)

    async def _history(self, session: str, *, user=TEST_USER_ID) -> dict:
        # Deliberately do not flush EventPersister: normal GET must observe the
        # independently committed state while the runner is still blocked.
        response = await self.http.get(
            f"/api/v1/sessions/{session}/history", params={"agent_id": self.agent_id},
            headers={"user": user},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def _events(self, session: str) -> list[dict]:
        response = await self.http.get(
            f"/api/v1/sessions/{session}/events", params={"agent_id": self.agent_id},
        )
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["items"]

    async def _finish(self, task: asyncio.Task, release: asyncio.Event | None = None) -> None:
        if release is not None:
            release.set()
        await asyncio.wait_for(asyncio.shield(task), timeout=5)

    def _canonical_once(self, history: dict, *, answers: list[str], calls: list[str]) -> None:
        self.assertEqual(len(_kind(history, "user_message")), 1)
        self.assertEqual([item["text"] for item in _kind(history, "assistant_message") if item["text"]], answers)
        self.assertEqual([item["call_id"] for item in _kind(history, "tool")], calls)
        ids = [item["item_id"] for item in history["items"]]
        self.assertEqual(len(ids), len(set(ids)))

    async def test_completed_round_visible_over_http_before_second_model_returns(self):
        session = "progress-second-model"
        ready, release = asyncio.Event(), asyncio.Event()
        requests = 0

        async def read_value() -> str:
            return "READ_RESULT"

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            if requests == 1:
                yield "ROUND_ONE"
                yield _call("read_value", "read-one")
            else:
                ready.set()
                await release.wait()
                yield "FINAL_ANSWER"

        task, events = self._start(self._runner(stream, tools=[read_value]), session)
        await self._ready(ready, task)
        history = await self._history(session)
        self.assertFalse(task.done())
        self._canonical_once(history, answers=["ROUND_ONE"], calls=["read-one"])
        tool = _kind(history, "tool")[0]
        self.assertEqual((tool["status"], tool["output"]), ("completed", "READ_RESULT"))
        denied = await self.http.get(f"/api/v1/sessions/{session}/history", headers={"user": "not-the-owner"})
        self.assertEqual(denied.status_code, 404)
        await self._finish(task, release)
        self.assertEqual(events[-1]["type"], "final")
        self._canonical_once(await self._history(session), answers=["ROUND_ONE", "FINAL_ANSWER"], calls=["read-one"])
        stored = await self._events(session)
        self.assertEqual(len([item for item in stored if item["event_type"] == "user/message"]), 1)
        self.assertFalse(any(item["event_type"] == "run/progress" and item["status"] == "running" for item in stored))

    async def test_fast_parallel_result_visible_while_sibling_tool_waits(self):
        session = "progress-parallel-tools"
        slow_started, fast_published, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        requests = 0

        async def fast_read() -> str:
            return "FAST_RESULT"

        async def slow_read() -> str:
            slow_started.set()
            await release.wait()
            return "SLOW_RESULT"

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            if requests == 1:
                yield "READING_IN_PARALLEL"
                yield {
                    0: DeltaToolCall(name="fast_read", json_args="{}", tool_call_id="fast"),
                    1: DeltaToolCall(name="slow_read", json_args="{}", tool_call_id="slow"),
                }
            else:
                yield "PARALLEL_DONE"

        async def observe(event):
            if event["type"] == "tool_result" and event.get("tool_call_id") == "fast":
                fast_published.set()

        task, _ = self._start(self._runner(stream, tools=[fast_read, slow_read]), session, on_event=observe)
        await self._ready(slow_started, task)
        await self._ready(fast_published, task)
        history = await self._history(session)
        self.assertFalse(task.done())
        tools = {item["call_id"]: item for item in _kind(history, "tool")}
        self.assertEqual(set(tools), {"fast", "slow"})
        self.assertEqual((tools["fast"]["status"], tools["fast"]["output"]), ("completed", "FAST_RESULT"))
        self.assertIsNone(tools["slow"]["output"])
        self.assertNotIn(tools["slow"]["status"], {"completed", "awaiting_input"})
        await self._finish(task, release)
        final = await self._history(session)
        self._canonical_once(final, answers=["READING_IN_PARALLEL", "PARALLEL_DONE"], calls=["fast", "slow"])
        self.assertTrue(all(item["status"] == "completed" for item in _kind(final, "tool")))

    async def test_partial_text_is_durable_during_provider_silence(self):
        session = "progress-provider-silence"
        ready, release = asyncio.Event(), asyncio.Event()

        async def stream(messages, info):
            yield "PARTIAL_PREFIX"
            yield "_MIDDLE"
            ready.set()
            await release.wait()
            yield "_FINISHED"

        task, _ = self._start(self._runner(stream), session)
        await self._ready(ready, task)
        async with asyncio.timeout(3):
            while True:
                history = await self._history(session)
                if any(item.get("text") == "PARTIAL_PREFIX_MIDDLE" for item in _kind(history, "assistant_message")):
                    break
                await asyncio.sleep(0.05)
        self.assertFalse(task.done())
        self.assertEqual(len(_kind(history, "user_message")), 1)
        await self._finish(task, release)
        self._canonical_once(await self._history(session), answers=["PARTIAL_PREFIX_MIDDLE_FINISHED"], calls=[])

    async def test_progress_write_failure_prevents_next_side_effect_tool(self):
        session = "progress-write-failure"
        requests = 0
        executed: list[str] = []
        original_save = self.store.save_run_progress
        rejected = False

        async def safe_read() -> str:
            executed.append("read")
            return "READ_BEFORE_FAILURE"

        async def side_effect() -> str:
            executed.append("SIDE_EFFECT_MUST_NOT_RUN")
            return "unreachable"

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            yield "FIRST_ROUND" if requests == 1 else "SECOND_ROUND"
            yield _call("safe_read" if requests == 1 else "side_effect", "read" if requests == 1 else "write")

        async def fail_before_side_effect(*args, **kwargs):
            nonlocal rejected
            if any(
                isinstance(message, ModelResponse)
                and any(isinstance(part, ToolCallPart) and part.tool_name == "side_effect" for part in message.parts)
                for message in kwargs["messages"]
            ):
                rejected = True
                raise OSError("synthetic progress database unavailable")
            return await original_save(*args, **kwargs)

        with patch.object(self.store, "save_run_progress", side_effect=fail_before_side_effect):
            task, events = self._start(self._runner(stream, tools=[safe_read, side_effect]), session)
            await self._finish(task)
        self.assertTrue(rejected)
        self.assertEqual(executed, ["read"])
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(self.ctx.active_runs.active_count(), 0)
        history = await self._history(session)
        self.assertEqual(len(_kind(history, "user_message")), 1)
        tool = next(item for item in _kind(history, "tool") if item["call_id"] == "read")
        self.assertEqual(tool["output"], "READ_BEFORE_FAILURE")

    async def _verify_heartbeat_during_silence(self, *, in_tool: bool) -> None:
        session = "progress-heartbeat-tool" if in_tool else "progress-heartbeat-provider"
        ready, release, renewed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_touch = self.store.touch_run_progress
        heartbeat_count = requests = 0

        async def trace_touch(session_id: str, *, run_id: str) -> bool:
            # Keep the real coordinator's keyword-only contract; an AsyncMock
            # accepting arbitrary positional arguments would hide a live bug.
            nonlocal heartbeat_count
            before = next(row for row in await self.store.list_all_events(session_id)
                          if row.event_type == "run/progress")
            result = await original_touch(session_id, run_id=run_id)
            after = next(row for row in await self.store.list_all_events(session_id)
                         if row.event_type == "run/progress")
            self.assertTrue(result)
            self.assertEqual((after.event_id, after.sequence, after.payload["revision"], after.payload["messages"]),
                             (before.event_id, before.sequence, before.payload["revision"], before.payload["messages"]))
            heartbeat_count += 1
            if heartbeat_count >= 2:
                renewed.set()
            return result

        async def slow_read() -> str:
            ready.set()
            await release.wait()
            return "SLOW_RESULT_AFTER_HEARTBEATS"

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            if in_tool:
                if requests == 1:
                    yield "BEFORE_SLOW_TOOL"
                    yield _call("slow_read", "slow-heartbeat")
                else:
                    yield "TOOL_FINISHED"
            else:
                yield "BEFORE_PROVIDER_SILENCE"
                ready.set()
                await release.wait()
                yield "_FINISHED"

        with patch.object(RunProgressCapability, "snapshot_interval", 0.025), \
                patch.object(RunProgressCapability, "heartbeat_interval", 0.05), \
                patch.object(self.store, "touch_run_progress", side_effect=trace_touch):
            task, events = self._start(self._runner(stream, tools=[slow_read] if in_tool else []), session)
            await self._ready(ready, task)
            await self._ready(renewed, task)
            self.assertFalse(task.done())
            self.assertGreaterEqual(heartbeat_count, 2)
            self.assertFalse(any(event["type"] == "error" for event in events))
            self.assertEqual(len(_kind(await self._history(session), "user_message")), 1)
            await self._finish(task, release)
        self.assertEqual(events[-1]["type"], "final")
        self._canonical_once(
            await self._history(session),
            answers=["BEFORE_SLOW_TOOL", "TOOL_FINISHED"] if in_tool else ["BEFORE_PROVIDER_SILENCE_FINISHED"],
            calls=["slow-heartbeat"] if in_tool else [],
        )

    async def test_provider_silence_renews_real_coordinator_lease(self):
        await self._verify_heartbeat_during_silence(in_tool=False)

    async def test_long_tool_renews_real_coordinator_lease(self):
        await self._verify_heartbeat_during_silence(in_tool=True)

    async def test_lost_lease_fails_run_preserving_history_without_next_side_effect(self):
        session = "progress-lost-lease"
        ready, revoked, closed = asyncio.Event(), asyncio.Event(), asyncio.Event()
        original_touch = self.store.touch_run_progress
        requests = 0
        executed: list[str] = []

        async def touch_until_revoked(session_id: str, *, run_id: str) -> bool:
            if revoked.is_set():
                return False
            return await original_touch(session_id, run_id=run_id)

        async def first_read() -> str:
            executed.append("read")
            return "DURABLE_BEFORE_LOST_LEASE"

        async def waiting_read() -> str:
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
            return "unreachable"

        async def side_effect() -> str:
            executed.append("SIDE_EFFECT_MUST_NOT_RUN")
            return "unreachable"

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            name = ["first_read", "waiting_read", "side_effect"][requests - 1]
            yield f"ROUND_{requests}"
            yield _call(name, name)

        with patch.object(RunProgressCapability, "snapshot_interval", 0.025), \
                patch.object(RunProgressCapability, "heartbeat_interval", 0.05), \
                patch.object(self.store, "touch_run_progress", side_effect=touch_until_revoked):
            task, events = self._start(self._runner(stream, tools=[first_read, waiting_read, side_effect]), session)
            await self._ready(ready, task)
            self.assertEqual(next(item for item in _kind(await self._history(session), "tool")
                                  if item["call_id"] == "first_read")["output"], "DURABLE_BEFORE_LOST_LEASE")
            revoked.set()
            await self._finish(task)
        self.assertTrue(closed.is_set())
        self.assertEqual(executed, ["read"])
        self.assertEqual(requests, 2)
        self.assertEqual(events[-1]["type"], "error")
        self.assertFalse(any(event["type"] in {"final", "cancelled"} for event in events))
        self.assertEqual(self.ctx.active_runs.active_count(), 0)
        history = await self._history(session)
        self._canonical_once(history, answers=["ROUND_1", "ROUND_2"], calls=["first_read", "waiting_read"])
        self.assertEqual(_kind(history, "tool")[0]["output"], "DURABLE_BEFORE_LOST_LEASE")
        self.assertFalse(any(item["status"] in {"running", "pending", "awaiting_input"} for item in history["items"]))
        rows = await self._events(session)
        progress = next(item for item in rows if item["event_type"] == "run/progress")
        self.assertEqual(progress["status"], "failed")

    async def test_unvalidated_ui_preview_is_not_actionable_until_pending_commit(self):
        session = "progress-ui-preview"
        ready, release = asyncio.Event(), asyncio.Event()
        published_histories: list[dict] = []

        async def stream(messages, info):
            yield "PLEASE_CONFIRM"
            yield _call("builtin__confirm", "confirm-once", {"message": "Synthetic confirmation"})
            ready.set()
            await release.wait()

        async def observe(event):
            if event["type"] == "tool_call" and event.get("tool_call_id") == "confirm-once":
                published_histories.append(await self._history(session))

        task, events = self._start(self._runner(stream, ui=True), session, on_event=observe)
        await self._ready(ready, task)
        history = await self._history(session)
        self.assertFalse(any(item["status"] == "awaiting_input" for item in history["items"]))
        self.assertFalse(any(event["type"] == "tool_call" for event in events))
        await self._finish(task, release)
        self.assertEqual(events[-1]["status"], "pending")
        self.assertEqual(len(published_histories), 1)
        tools = _kind(published_histories[0], "tool")
        self.assertEqual([(item["call_id"], item["status"]) for item in tools], [("confirm-once", "awaiting_input")])
        self._canonical_once(await self._history(session), answers=["PLEASE_CONFIRM"], calls=["confirm-once"])

    async def test_http_cancel_preserves_completed_result_and_closes_waiting_tool(self):
        session = "progress-cancel"
        ready, exited = asyncio.Event(), asyncio.Event()
        requests = 0

        async def first_read() -> str:
            return "COMPLETED_BEFORE_STOP"

        async def waiting_read() -> str:
            ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                exited.set()
            return "unreachable"

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            yield "STEP_ONE" if requests == 1 else "STEP_TWO"
            yield _call("first_read" if requests == 1 else "waiting_read", "finished" if requests == 1 else "waiting")

        task, events = self._start(self._runner(stream, tools=[first_read, waiting_read]), session)
        await self._ready(ready, task)
        during = await self._history(session)
        self.assertEqual(next(item for item in _kind(during, "tool") if item["call_id"] == "finished")["output"], "COMPLETED_BEFORE_STOP")
        response = await self.http.post(f"/api/v1/sessions/{session}/cancel", params={"agent_id": self.agent_id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "cancel_requested")
        await self._finish(task)
        self.assertTrue(exited.is_set())
        self.assertEqual(events[-1]["type"], "cancelled")
        self.assertEqual(self.ctx.active_runs.active_count(), 0)
        history = await self._history(session)
        tools = {item["call_id"]: item for item in _kind(history, "tool")}
        self.assertEqual(tools["finished"]["output"], "COMPLETED_BEFORE_STOP")
        self.assertEqual(tools["waiting"]["status"], "interrupted")
        self.assertFalse(any(item["status"] in {"running", "pending", "awaiting_input"} for item in history["items"]))

    async def test_provider_failure_after_durable_round_does_not_lose_or_duplicate_it(self):
        session = "progress-provider-failure"
        ready, release = asyncio.Event(), asyncio.Event()
        requests = 0

        async def read_value() -> str:
            return "PRESERVED_RESULT"

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            if requests == 1:
                yield "PRESERVED_TEXT"
                yield _call("read_value", "preserved")
            else:
                ready.set()
                await release.wait()
                raise RuntimeError("synthetic later provider failure")

        task, events = self._start(self._runner(stream, tools=[read_value]), session)
        await self._ready(ready, task)
        self._canonical_once(await self._history(session), answers=["PRESERVED_TEXT"], calls=["preserved"])
        await self._finish(task, release)
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(self.ctx.active_runs.active_count(), 0)
        history = await self._history(session)
        self._canonical_once(history, answers=["PRESERVED_TEXT"], calls=["preserved"])
        self.assertEqual(_kind(history, "tool")[0]["output"], "PRESERVED_RESULT")
        self.assertFalse(any(item["status"] in {"running", "pending", "awaiting_input"} for item in history["items"]))

    def _reject_terminal_appends(self):
        original = self.store.append

        async def reject(*args, **kwargs):
            if kwargs.get("terminal_status") is not None:
                raise OSError("synthetic immutable terminal append failure")
            return await original(*args, **kwargs)

        return patch.object(self.store, "append", side_effect=reject)

    async def _assert_fallback_closed(self, session, *, status):
        stored = await self.store.list_all_events(session)
        progress = [row for row in stored if row.event_type == "run/progress"]
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0].status, status)
        self.assertNotIn("messages", progress[0].payload)
        self.assertFalse(any(row.status == "running" for row in stored))
        self.assertEqual(self.ctx.active_runs.active_count(), 0)

    async def test_final_append_failure_uses_durable_fallback_and_never_reports_final(self):
        session = "progress-final-append-failure"
        requests = executed = 0

        async def read_value() -> str:
            nonlocal executed
            executed += 1
            return "RESULT_BEFORE_APPEND_FAILURE"

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            if requests == 1:
                yield "ROUND_BEFORE_APPEND_FAILURE"
                yield _call("read_value", "kept-call")
            else:
                yield "FINAL_BEFORE_APPEND_FAILURE"

        with self._reject_terminal_appends():
            task, events = self._start(self._runner(stream, tools=[read_value]), session)
            await self._finish(task)
        self.assertEqual((requests, executed), (2, 1))
        self.assertEqual(events[-1]["type"], "error")
        self.assertFalse(any(event["type"] == "final" for event in events))
        history = await self._history(session)
        self._canonical_once(history, answers=["ROUND_BEFORE_APPEND_FAILURE", "FINAL_BEFORE_APPEND_FAILURE"], calls=["kept-call"])
        self.assertEqual(_kind(history, "tool")[0]["output"], "RESULT_BEFORE_APPEND_FAILURE")
        await self._assert_fallback_closed(session, status="failed")

    async def test_cancel_append_failure_reports_incomplete_persistence_but_seals_checkpoint(self):
        session = "progress-cancel-append-failure"
        ready, closed = asyncio.Event(), asyncio.Event()
        requests = 0

        async def read_value() -> str:
            return "RESULT_BEFORE_CANCEL_APPEND_FAILURE"

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            if requests == 1:
                yield "DURABLE_BEFORE_CANCEL_APPEND_FAILURE"
                yield _call("read_value", "kept-before-cancel")
            else:
                ready.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    closed.set()

        with self._reject_terminal_appends():
            task, events = self._start(self._runner(stream, tools=[read_value]), session)
            await self._ready(ready, task)
            response = await self.http.post(f"/api/v1/sessions/{session}/cancel", params={"agent_id": self.agent_id})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "cancel_requested")
            await self._finish(task)
        self.assertTrue(closed.is_set())
        self.assertEqual(events[-1]["type"], "cancelled")
        self.assertFalse(events[-1]["history_persisted"])
        history = await self._history(session)
        self._canonical_once(history, answers=["DURABLE_BEFORE_CANCEL_APPEND_FAILURE"], calls=["kept-before-cancel"])
        self.assertEqual(_kind(history, "tool")[0]["output"], "RESULT_BEFORE_CANCEL_APPEND_FAILURE")
        await self._assert_fallback_closed(session, status="interrupted")

    async def test_nonstream_final_append_failure_raises_original_and_closes_lifecycle(self):
        session = "progress-nonstream-append-failure"
        requests = executed = 0

        async def read_value() -> str:
            nonlocal executed
            executed += 1
            return "NONSTREAM_DURABLE_RESULT"

        async def answer(messages, info):
            nonlocal requests
            requests += 1
            if requests == 1:
                return ModelResponse(parts=[TextPart("NONSTREAM_ROUND"), ToolCallPart("read_value", {}, "nonstream-call")])
            return ModelResponse(parts=[TextPart("NONSTREAM_FINAL")])

        runner = AgentRunner(
            Agent(FunctionModel(function=answer), deps_type=AgentDeps,
                  toolsets=[FunctionToolset(tools=[read_value])]),
            self.store, UsageLimits(request_limit=4), active_runs=self.ctx.active_runs,
        )
        with self._reject_terminal_appends(), self.assertRaisesRegex(OSError, "synthetic immutable terminal append failure"):
            await asyncio.wait_for(runner.run(session, "synthetic nonstream verification", AgentDeps(
                session_id=session, user=TEST_USER_ID, agent_id=self.agent_id,
            )), timeout=5)
        self.assertEqual((requests, executed), (2, 1))
        history = await self._history(session)
        self._canonical_once(history, answers=["NONSTREAM_ROUND", "NONSTREAM_FINAL"], calls=["nonstream-call"])
        self.assertEqual(_kind(history, "tool")[0]["output"], "NONSTREAM_DURABLE_RESULT")
        await self._assert_fallback_closed(session, status="failed")
