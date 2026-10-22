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
from httpx2 import DecodingError
from pydantic_ai import Agent, FunctionToolset, ModelRetry
from pydantic_ai.messages import BinaryContent, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturn, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
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


class IncrementalMessagePersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory(
            prefix="TMP_to_delete-message-persistence-", dir=Path(__file__).resolve().parents[2],
        )
        stores, engine, factory = await make_sqlite_stores(
            f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'history.sqlite3'}",
        )
        self.agent_id = await seed_default_agent(stores, name="isolated-message-history")
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
                session, "synthetic completed-message verification", deps, persister=self.ctx.event_persister,
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
        session = "facts-second-model"
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
        self.assertFalse(any(item["event_type"].startswith("run/") for item in stored))
        self.assertFalse(any(item["event_type"] == "execution/error" for item in stored))

    async def test_fast_parallel_result_visible_while_sibling_tool_waits(self):
        session = "facts-parallel-tools"
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
        self.assertFalse(any(item["event_type"].startswith("run/") for item in await self._events(session)))

    async def test_partial_text_stays_live_only_until_response_completes(self):
        session = "facts-provider-silence"
        ready, release = asyncio.Event(), asyncio.Event()

        async def stream(messages, info):
            yield "PARTIAL_PREFIX"
            yield "_MIDDLE"
            ready.set()
            await release.wait()
            yield "_FINISHED"

        task, _ = self._start(self._runner(stream), session)
        await self._ready(ready, task)
        await asyncio.sleep(0.2)
        history = await self._history(session)
        self.assertEqual(_kind(history, "assistant_message"), [])
        self.assertFalse(task.done())
        self.assertEqual(len(_kind(history, "user_message")), 1)
        await self._finish(task, release)
        self._canonical_once(await self._history(session), answers=["PARTIAL_PREFIX_MIDDLE_FINISHED"], calls=[])

    async def test_message_write_failure_prevents_next_side_effect_tool(self):
        session = "facts-write-failure"
        requests = 0
        executed: list[str] = []
        original_save = self.store.append_message_parts
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
                raise OSError("synthetic message database unavailable")
            return await original_save(*args, **kwargs)

        with patch.object(self.store, "append_message_parts", side_effect=fail_before_side_effect):
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

    async def test_unvalidated_ui_preview_is_not_actionable_until_pending_commit(self):
        session = "facts-ui-preview"
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
        session = "facts-cancel"
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
        stored = await self._events(session)
        self.assertFalse(any(row["event_type"].startswith("run/") or row["event_type"] == "execution/error" for row in stored))
        self.assertEqual(self.ctx.active_runs.active_count(), 0)
        history = await self._history(session)
        tools = {item["call_id"]: item for item in _kind(history, "tool")}
        self.assertEqual(tools["finished"]["output"], "COMPLETED_BEFORE_STOP")
        self.assertEqual(tools["waiting"]["status"], "interrupted")
        self.assertFalse(any(item["status"] in {"running", "pending", "awaiting_input"} for item in history["items"]))

    async def test_provider_failure_after_durable_round_does_not_lose_or_duplicate_it(self):
        session = "facts-provider-failure"
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
        stored = await self._events(session)
        self.assertFalse(any(row["event_type"].startswith("run/") for row in stored))
        failures = [row for row in stored if row["event_type"] == "execution/error"]
        self.assertEqual(len(failures), 1)

    async def test_first_provider_failure_has_one_error_without_empty_assistant(self):
        async def stream(messages, info):
            raise RuntimeError("synthetic first request failed")
            yield "unreachable"

        task, events = self._start(self._runner(stream), "first-provider-error")
        await self._finish(task)
        self.assertEqual(events[-1]["type"], "error")
        rows = await self._events("first-provider-error")
        self.assertEqual([row["event_type"] for row in rows if row["event_type"] != "session/title"],
                         ["user/message", "execution/error"])
        self.assertEqual(_kind(await self._history("first-provider-error"), "assistant_message"), [])

    async def test_partial_decode_failure_then_success_mirrors_durable_facts_before_terminal(self):
        session = "partial-decoding-error-then-success"

        async def broken_stream(messages, info):
            yield "SYNTHETIC_PARTIAL"
            raise DecodingError("synthetic compressed response failure")

        observed = []

        async def observe(event):
            if event["type"] == "error":
                # The client must have the same failed response/status by the
                # time it sees the terminal error, without a refresh or flush.
                rows = await self._events(session)
                saved = next(row for row in rows if row["event_type"] == "assistant/message")
                observed.append(saved)

        task, failed = self._start(self._runner(broken_stream), session, on_event=observe)
        await self._finish(task)
        saved = observed[0]
        live = {frame["event"]["event_id"]: frame["event"] for frame in failed
                if frame["type"] in {"session_event_appended", "session_event_updated"}}
        self.assertEqual({key: live[saved["event_id"]][key] for key in saved}, saved)
        self.assertEqual(saved["status"], "failed")
        self.assertEqual(failed[-1]["type"], "error")
        failure = next(row for row in await self._events(session) if row["event_type"] == "execution/error")
        self.assertEqual(failure["payload"]["message"], failed[-1]["message"])
        self.assertEqual(saved["payload"]["message"]["metadata"]["deepagent_error"]["message"], failed[-1]["message"])

        async def good_stream(messages, info):
            yield "SYNTHETIC_RECOVERY"

        task, recovered = self._start(self._runner(good_stream), session)
        await self._finish(task)
        self.assertEqual(recovered[-1]["type"], "final")
        rows = await self._events(session)
        for frame in recovered:
            if frame["type"] in {"session_event_appended", "session_event_updated"}:
                live[frame["event"]["event_id"]] = frame["event"]
        self.assertEqual(set(live), {row["event_id"] for row in rows})
        self.assertEqual([
            {key: frame[key] for key in rows[index]}
            for index, frame in enumerate(sorted(live.values(), key=lambda row: row["sequence"]))
        ], rows)
        self.assertEqual(len([row for row in rows if row["event_type"] == "execution/error"]), 1)
        assistants = _kind(await self._history(session), "assistant_message")
        self.assertEqual([item["text"] for item in assistants], ["SYNTHETIC_PARTIAL", "SYNTHETIC_RECOVERY"])
        self.assertEqual([item["status"] for item in assistants], ["interrupted", "completed"])
        self.assertEqual(self.ctx.active_runs.active_count(), 0)

    async def test_recoverable_tool_failure_does_not_create_execution_error(self):
        requests = executions = 0

        async def retry_read() -> str:
            nonlocal executions
            executions += 1
            if executions == 1:
                raise ModelRetry("synthetic recoverable read failure")
            return "RECOVERED_RESULT"

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            if requests < 3:
                yield f"READ_ATTEMPT_{requests}"
                yield _call("retry_read", f"attempt-{requests}")
            else:
                yield "RECOVERED_FINAL"

        task, events = self._start(self._runner(stream, tools=[retry_read]), "tool-retry-facts")
        await self._finish(task)
        self.assertEqual((requests, executions), (3, 2))
        self.assertEqual(events[-1]["type"], "final")
        history = await self._history("tool-retry-facts")
        tools = _kind(history, "tool")
        self.assertEqual(len(tools), 2)
        # ModelRetry is the existing rejected/retry attempt, rather than an
        # execution-level failure; its real retry reason remains in the events.
        self.assertEqual(tools[0]["status"], "removed")
        self.assertEqual(tools[1]["output"], "RECOVERED_RESULT")
        self.assertIn("synthetic recoverable read failure", str(await self._events("tool-retry-facts")))
        self.assertFalse(any(row["event_type"].startswith("run/") or row["event_type"] == "execution/error"
                             for row in await self._events("tool-retry-facts")))

    async def test_parallel_reverse_completion_keeps_both_results_and_model_group(self):
        first_started, second_committed, release = asyncio.Event(), asyncio.Event(), asyncio.Event()
        requests = 0

        async def first() -> str:
            first_started.set()
            await release.wait()
            return "FIRST_RESULT"

        async def second() -> str:
            await first_started.wait()
            return "SECOND_RESULT"

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            if requests == 1:
                yield "PARALLEL_START"
                yield {0: DeltaToolCall(name="first", json_args="{}", tool_call_id="first"),
                       1: DeltaToolCall(name="second", json_args="{}", tool_call_id="second")}
            else:
                results = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
                self.assertEqual({part.tool_call_id: part.content for part in results},
                                 {"first": "FIRST_RESULT", "second": "SECOND_RESULT"})
                yield "BOTH_DONE"

        async def observe(event):
            if event["type"] == "tool_result" and event.get("tool_call_id") == "second":
                second_committed.set()

        session = "parallel-reverse-completion"
        task, events = self._start(self._runner(stream, tools=[first, second]), session, on_event=observe)
        await self._ready(second_committed, task)
        before = await self._events(session)
        visible = {item["call_id"]: item for item in _kind(await self._history(session), "tool")}
        self.assertEqual(visible["second"]["output"], "SECOND_RESULT")
        self.assertIsNone(visible["first"]["output"])
        await self._finish(task, release)
        self.assertEqual(events[-1]["type"], "final")
        final = {row["event_id"]: row for row in await self._events(session)}
        # Previously committed facts keep their identities, sequence and payload;
        # only a real terminal status may be attached on completion.
        for row in before:
            self.assertEqual(final[row["event_id"]]["sequence"], row["sequence"])
            if "message" in row["payload"]:
                self.assertEqual(final[row["event_id"]]["payload"]["message"]["parts"],
                                 row["payload"]["message"]["parts"])
        self._canonical_once(await self._history(session), answers=["PARALLEL_START", "BOTH_DONE"], calls=["first", "second"])

    async def test_identical_text_and_reused_call_ids_in_different_rounds_are_not_deduplicated(self):
        requests = executions = 0

        async def read() -> str:
            nonlocal executions
            executions += 1
            return f"RESULT_{executions}"

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            yield "IDENTICAL_TEXT"
            if requests < 3:
                yield _call("read", "reused-provider-id")

        session = "identical-facts-distinct-rounds"
        task, events = self._start(self._runner(stream, tools=[read]), session)
        await self._finish(task)
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual((requests, executions), (3, 2))
        history = await self._history(session)
        self._canonical_once(history, answers=["IDENTICAL_TEXT"] * 3,
                             calls=["reused-provider-id", "reused-provider-id"])
        self.assertEqual([item["output"] for item in _kind(history, "tool")], ["RESULT_1", "RESULT_2"])

    async def test_nonstream_runner_commits_each_round_before_next_request(self):
        ready, release = asyncio.Event(), asyncio.Event()
        requests = 0

        async def read() -> str:
            return "NONSTREAM_RESULT"

        async def answer(messages, info):
            nonlocal requests
            requests += 1
            if requests == 1:
                return ModelResponse(parts=[TextPart("NONSTREAM_ROUND"), ToolCallPart("read", {}, "call")])
            ready.set()
            await release.wait()
            return ModelResponse(parts=[TextPart("NONSTREAM_FINAL")])

        session = "nonstream-round-boundary"
        runner = AgentRunner(Agent(FunctionModel(function=answer), deps_type=AgentDeps,
                                   toolsets=[FunctionToolset(tools=[read])]),
                             self.store, UsageLimits(request_limit=4), active_runs=self.ctx.active_runs)
        task = asyncio.create_task(runner.run(session, "synthetic nonstream verification", AgentDeps(
            session_id=session, user=TEST_USER_ID, agent_id=self.agent_id,
        )))
        self.tasks.append(task)
        await self._ready(ready, task)
        during = await self._history(session)
        self._canonical_once(during, answers=["NONSTREAM_ROUND"], calls=["call"])
        self.assertEqual(_kind(during, "tool")[0]["output"], "NONSTREAM_RESULT")
        self.assertFalse(task.done())
        await self._finish(task, release)
        self._canonical_once(await self._history(session), answers=["NONSTREAM_ROUND", "NONSTREAM_FINAL"], calls=["call"])
        self.assertFalse(any(row["event_type"].startswith("run/") or row["event_type"] == "execution/error"
                             for row in await self._events(session)))

    async def test_provider_http_error_persists_safe_classification_and_not_model_history(self):
        secret = "SYNTHETIC_PROVIDER_KEY_NOT_FOR_STORAGE"

        async def stream(messages, info):
            raise ModelHTTPError(403, "synthetic-model", body={"access_token": secret, "prompt": "PRIVATE_PROMPT"},
                                 headers={"Authorization": f"Bearer {secret}"})
            yield "unreachable"

        session = "classified-provider-error"
        task, events = self._start(self._runner(stream), session)
        await self._finish(task)
        self.assertEqual(events[-1]["type"], "error")
        rows = await self._events(session)
        failures = [row for row in rows if row["event_type"] == "execution/error"]
        self.assertEqual(len(failures), 1)
        payload = failures[0]["payload"]
        self.assertEqual((payload["code"], payload["phase"]), ("provider_http_error", "provider"))
        self.assertIn("403", payload["message"])
        self.assertNotIn(secret, json.dumps(rows))
        self.assertNotIn("PRIVATE_PROMPT", json.dumps(rows))
        model_history = await self.store.get_history(session, agent_id=self.agent_id)
        self.assertEqual(len(model_history), 1)
        self.assertNotIn("provider_http_error", str(model_history))

    async def test_tool_validation_exhaustion_has_one_specific_error_and_no_execution(self):
        executed = requests = 0

        async def integer_read(number: int) -> str:
            nonlocal executed
            executed += 1
            return str(number)

        async def stream(messages, info):
            nonlocal requests
            requests += 1
            yield _call("integer_read", f"invalid-{requests}", {"number": "not-an-integer"})

        session = "classified-tool-exhaustion"
        task, events = self._start(self._runner(stream, tools=[integer_read]), session)
        await self._finish(task)
        self.assertEqual(events[-1]["type"], "error")
        self.assertEqual(executed, 0)
        self.assertGreater(requests, 1)
        rows = await self._events(session)
        failures = [row for row in rows if row["event_type"] == "execution/error"]
        self.assertEqual(len(failures), 1)
        payload = failures[0]["payload"]
        self.assertEqual((payload["code"], payload["phase"]), ("tool_retry_exhausted", "tool_validation"))
        self.assertIn("integer_read", payload["message"])
        self.assertNotIn("not-an-integer", json.dumps(payload))
        model_history = await self.store.get_history(session, agent_id=self.agent_id)
        # Only model message parts become conversation content. Safe diagnostic
        # metadata on a failed response is not another model message/event.
        self.assertNotIn("tool_retry_exhausted", str([message.parts for message in model_history]))
        self.assertNotIn("execution/error", str([message.parts for message in model_history]))

    async def test_nonstream_parallel_tool_content_keeps_official_request_group(self):
        slow_started, release = asyncio.Event(), asyncio.Event()
        requests = 0
        media = BinaryContent(data=b"synthetic-local-image-bytes", media_type="image/png")

        async def media_read() -> ToolReturn:
            return ToolReturn("MEDIA_RESULT", content=["SUPPLEMENTAL_CONTENT", media], metadata={"safe": True})

        async def slow_read() -> str:
            slow_started.set()
            await release.wait()
            return "SLOW_RESULT"

        async def answer(messages, info):
            nonlocal requests
            requests += 1
            if requests == 1:
                return ModelResponse(parts=[TextPart("READING_MEDIA"),
                                            ToolCallPart("media_read", {}, "media"),
                                            ToolCallPart("slow_read", {}, "slow")])
            return ModelResponse(parts=[TextPart("MEDIA_FINAL")])

        session = "nonstream-parallel-content"
        runner = AgentRunner(Agent(FunctionModel(function=answer), deps_type=AgentDeps,
                                   toolsets=[FunctionToolset(tools=[media_read, slow_read])]),
                             self.store, UsageLimits(request_limit=4), active_runs=self.ctx.active_runs)
        task = asyncio.create_task(runner.run(session, "synthetic media verification", AgentDeps(
            session_id=session, user=TEST_USER_ID, agent_id=self.agent_id,
        )))
        self.tasks.append(task)
        await self._ready(slow_started, task)
        async with asyncio.timeout(3):
            while True:
                history = await self._history(session)
                tools = {item["call_id"]: item for item in _kind(history, "tool")}
                if tools.get("media", {}).get("output") == "MEDIA_RESULT":
                    break
                await asyncio.sleep(0.005)
        self.assertFalse(task.done())
        self.assertIsNone(tools["slow"]["output"])
        await self._finish(task, release)
        stored_history = await self.store.get_raw_history(session, agent_id=self.agent_id)
        result_requests = [message for message in stored_history if isinstance(message, ModelRequest)
                           and any(isinstance(part, ToolReturnPart) for part in message.parts)]
        self.assertEqual(len(result_requests), 1)
        request = result_requests[0]
        results = [part for part in request.parts if isinstance(part, ToolReturnPart)]
        self.assertEqual({part.tool_call_id for part in results}, {"media", "slow"})
        self.assertEqual(next(part for part in results if part.tool_call_id == "media").metadata, {"safe": True})
        contents = [part for part in request.parts if isinstance(part, UserPromptPart)]
        self.assertEqual(len(contents), 1)
        self.assertIsInstance(contents[0].content, list)
        self.assertEqual(len(contents[0].content), 2)
        self.assertEqual(contents[0].content[0], "SUPPLEMENTAL_CONTENT")
        restored_media = contents[0].content[1]
        # The SDK codec narrows image/png BinaryContent to BinaryImage and
        # derives its identifier; preserve exact bytes, MIME and content order
        # without requiring the pre-serialization Python subclass to survive.
        self.assertIsInstance(restored_media, BinaryContent)
        self.assertEqual(restored_media.data, media.data)
        self.assertEqual(restored_media.media_type, media.media_type)
        self.assertEqual(restored_media.identifier, media.identifier)
        self.assertEqual(len(_kind(await self._history(session), "user_message")), 1)
