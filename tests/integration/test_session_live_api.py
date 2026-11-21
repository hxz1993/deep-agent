"""Real ASGI SSE observation, real Runner and isolated committed SQL history.

The tiny ASGI client exposes each response frame immediately rather than using
HTTPX ASGITransport, which buffers an SSE response until execution has ended.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlencode
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request
from httpx import ASGITransport, AsyncClient
from pydantic_ai import Agent, Tool
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits
from sqlalchemy import event as sql_event

from deep_agent.agent.context.compaction import DeepAgentContextCompaction, summary_contents
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.title_generator import TitleGenerationResult
from deep_agent.agent.tool_names import READ_ONLY_TOOL
from deep_agent.api.v1 import api_router
from deep_agent.api.v1.sessions import observe_session_run
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.registry import build_external_toolset
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.security import ConsoleAdminAccess
from deep_agent.skills.runtime import SkillRuntimeLibrary
from tests.helpers.request_identity import TEST_USER_ID
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent
from tests.unit.test_context_compaction import valid_summary


class _StreamingRequest:
    def __init__(self, app, path, *, method="GET", query=None, body=None, user=TEST_USER_ID):
        self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()
        self.buffer = ""
        raw = json.dumps(body).encode() if body is not None else b""
        self.incoming.put_nowait({"type": "http.request", "body": raw, "more_body": False})
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": method, "scheme": "http", "path": path,
                 "raw_path": path.encode(), "root_path": "", "query_string": urlencode(query or {}).encode(),
                 "headers": [(b"user", user.encode()), (b"content-type", b"application/json")],
                 "server": ("test", 80), "client": ("127.0.0.1", 12345)}
        self.task = asyncio.create_task(app(scope, self.incoming.get, self.outgoing.put))

    async def started(self):
        result = await asyncio.wait_for(self.outgoing.get(), 5)
        if result["type"] != "http.response.start":
            raise AssertionError(result)
        return result

    async def frame(self):
        async with asyncio.timeout(5):
            while "\n\n" not in self.buffer:
                message = await self.outgoing.get()
                if message["type"] != "http.response.body":
                    continue
                self.buffer += message.get("body", b"").decode().replace("\r\n", "\n")
                if not message.get("more_body") and not self.buffer:
                    raise EOFError("SSE ended")
            block, self.buffer = self.buffer.split("\n\n", 1)
            fields = dict(line.split(": ", 1) for line in block.splitlines() if ": " in line)
            return fields.get("event", "message"), json.loads(fields.get("data", "null"))

    async def until(self, event):
        frames = []
        while True:
            frame = await self.frame()
            frames.append(frame)
            if frame[0] == event:
                return frames

    async def close(self):
        if not self.task.done():
            self.incoming.put_nowait({"type": "http.disconnect"})
        try:
            await asyncio.wait_for(asyncio.shield(self.task), 5)
        except TimeoutError:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            raise


class SessionLiveApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="TMP_to_delete-session-live-", dir=Path(__file__).resolve().parents[2])
        stores, engine, factory = await make_sqlite_stores(f"sqlite+aiosqlite:///{Path(self.tmp.name) / 'live.sqlite3'}")
        self.agent_id = await seed_default_agent(stores, name="isolated-live-observation")
        self.ctx = AppContext.for_testing(registry=build_registry(), stores=stores, db_engine=engine,
                                          db_session_factory=factory,
                                          skill_runtime=SkillRuntimeLibrary(Path(self.tmp.name) / "skills"))
        init_context(self.ctx)
        await self.ctx.event_persister.start()
        self.app = FastAPI()
        self.app.include_router(api_router)
        self.http = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test", headers={"user": TEST_USER_ID})
        self.requests = []
        self.session = "isolated-observer-session"
        self.key = (self.agent_id, self.session, TEST_USER_ID)
        await self.ctx.store.append(self.session, [ModelRequest(parts=[UserPromptPart("synthetic prior input")])],
                                    agent_id=self.agent_id, user_id=TEST_USER_ID, run_id="prior-run")

    async def asyncTearDown(self):
        for request in reversed(self.requests):
            await request.close()
        await self.http.aclose()
        await self.ctx.aclose()
        reset_context()
        self.tmp.cleanup()

    def _request(self, path=None, **kwargs):
        request = _StreamingRequest(self.app, path or f"/api/v1/sessions/{self.session}/live", **kwargs)
        self.requests.append(request)
        return request

    async def test_diagnostic_live_read_requires_admin_and_keeps_original_owner(self):
        path = f"/api/v1/diagnostics/sessions/{self.session}/live"
        self.ctx.session_live.begin(self.key, "diagnostic-run", baseline_sequence=1)
        self.ctx.session_live.publish(self.key, "diagnostic-run", "text", {"delta": "observed text"})
        with patch.object(
            self.ctx.console_admin_authorizer, "access_for", return_value=ConsoleAdminAccess(allowed=False),
        ):
            forbidden = await self.http.get(path, params={"run_id": "diagnostic-run"})
            self.assertEqual(forbidden.status_code, 403)
        with patch.object(
            self.ctx.console_admin_authorizer, "access_for", return_value=ConsoleAdminAccess(allowed=True),
        ):
            stale = await self.http.get(path, params={"run_id": "different-run"})
            self.assertEqual(stale.status_code, 409)
            before_stream = self.ctx.active_runs.register(
                agent_id=self.agent_id, session_id=self.session, user_id=TEST_USER_ID,
                conversation_id="diagnostic-conversation", run_id="setting-up",
            )
            try:
                preparing = await self.http.get(path, params={"run_id": "setting-up"})
                self.assertEqual(preparing.status_code, 503)
            finally:
                self.ctx.active_runs.unregister(before_stream)
            observer = self._request(path, query={"run_id": "diagnostic-run"}, user="different-admin")
            self.assertEqual((await observer.started())["status"], 200)
            event, sync = await observer.frame()
            self.assertEqual((event, sync["session_id"], sync["baseline_sequence"]),
                             ("observation_sync", self.session, 1))
            event, frame = await observer.frame()
            self.assertEqual((event, frame["event"], frame["data"]["delta"]),
                             ("observation", "text", "observed text"))
            self.ctx.session_live.end(self.key, "diagnostic-run")
            self.assertEqual((await observer.frame())[0], "observation_end")

    async def test_auth_missing_nonowner_wrong_agent_and_unknown_session(self):
        self.ctx.session_live.begin(self.key, "active")
        path = f"/api/v1/sessions/{self.session}/live"
        query = {"agent_id": self.agent_id, "run_id": "active"}
        async with AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test") as anonymous:
            self.assertEqual((await anonymous.get(path, params=query)).status_code, 401)
        self.assertEqual((await self.http.get(path, params=query, headers={"user": "different-owner"})).status_code, 404)
        self.assertEqual((await self.http.get(path, params={**query, "agent_id": "wrong-agent"})).status_code, 404)
        self.assertEqual((await self.http.get("/api/v1/sessions/missing/live", params=query)).status_code, 404)
        self.assertEqual(self.ctx.session_live._subscribers, {})

    async def test_idle_stale_run_missing_run_and_capacity_status_codes(self):
        path = f"/api/v1/sessions/{self.session}/live"
        self.assertEqual((await self.http.get(path)).status_code, 422)
        self.assertEqual((await self.http.get(path, params={"run_id": "old"})).status_code, 409)
        self.ctx.session_live.begin(self.key, "active")
        self.assertEqual((await self.http.get(path, params={"run_id": "old"})).status_code, 409)
        self.ctx.session_live.max_subscribers = 0
        self.assertEqual((await self.http.get(path, params={"run_id": "active"})).status_code, 503)

    async def test_activity_stream_survives_two_runs_and_is_owner_scoped(self):
        path = f"/api/v1/sessions/{self.session}/activity"
        self.assertEqual((await self.http.get(path, headers={"user": "different-owner"})).status_code, 404)
        clients = [self._request(path, query={"agent_id": self.agent_id}) for _ in range(2)]
        for client in clients:
            self.assertEqual((await client.started())["status"], 200)
            self.assertEqual(await client.frame(), ("session_activity", {"status": "idle", "run_id": None}))
        for run_id in ("first", "second"):
            self.ctx.session_live.begin(self.key, run_id)
            for client in clients:
                self.assertEqual(await client.frame(), ("session_activity", {"status": "running", "run_id": run_id}))
            self.ctx.session_live.end(self.key, run_id)
            for client in clients:
                self.assertEqual(await client.frame(), ("session_activity", {"status": "idle", "run_id": run_id}))
        for client in clients:
            await client.close()
        self.assertFalse(self.ctx.session_live._activity)

    async def test_idle_activity_capacity_does_not_block_run_observation(self):
        self.ctx.session_live.max_subscribers = 1
        path = f"/api/v1/sessions/{self.session}/activity"
        activity = self._request(path)
        self.assertEqual((await activity.started())["status"], 200)
        self.assertEqual((await activity.frame())[1]["status"], "idle")
        self.assertEqual((await self.http.get(path)).status_code, 503)
        self.ctx.session_live.begin(self.key, "active")
        observer = self._request(query={"run_id": "active"})
        self.assertEqual((await observer.started())["status"], 200)
        self.assertEqual((await observer.frame())[0], "observation_sync")
        await observer.close()
        await activity.close()

    async def test_stop_for_old_run_does_not_cancel_new_run(self):
        entry = self.ctx.active_runs.register(agent_id=self.agent_id, session_id=self.session,
                                              user_id=TEST_USER_ID, run_id="current")
        try:
            path = f"/api/v1/sessions/{self.session}/cancel"
            params = {"agent_id": self.agent_id}
            response = await self.http.post(path, params={**params, "run_id": "previous"})
            self.assertEqual(response.json()["status"], "not_running")
            self.assertFalse(entry.cancellation_token.cancelled)
            response = await self.http.post(path, params={**params, "run_id": "current"})
            self.assertEqual(response.json()["status"], "cancel_requested")
            self.assertTrue(entry.cancellation_token.cancelled)
        finally:
            self.ctx.active_runs.unregister(entry)

    async def test_unavailable_replay_returns_409_instead_of_partial_prefix(self):
        self.ctx.session_live.max_replay_frames = 1
        self.ctx.session_live.begin(self.key, "active")
        for index in range(2):
            self.ctx.session_live.publish(self.key, "active", "text", {"delta": str(index)})
        response = await self.http.get(f"/api/v1/sessions/{self.session}/live", params={"run_id": "active"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("replay", response.json()["detail"].lower())

    async def test_observer_replay_resolves_file_references_split_between_frames(self):
        hub = self.ctx.session_live
        hub.begin(self.key, "active")
        hub.publish(self.key, "active", "tool_result", {
            "type": "tool_result", "artifacts": [{"file_id": "file123", "kind": "sandbox_output"}],
        })
        hub.publish(self.key, "active", "text", {"type": "text", "delta": "结果：[下载](attach"})
        observer = self._request(query={"run_id": "active"})
        self.assertEqual((await observer.started())["status"], 200)
        self.assertEqual((await observer.frame())[0], "observation_sync")
        self.assertEqual((await observer.frame())[1]["event"], "tool_result")
        self.assertEqual((await observer.frame())[1]["data"]["delta"], "结果：")
        hub.publish(self.key, "active", "text", {"type": "text", "delta": "ment://file123)"})
        self.assertEqual(
            (await observer.frame())[1]["data"]["delta"],
            "[下载](<http://test/deepfos-server/platform-file-server/files/download?id=file123>)",
        )
        hub.end(self.key, "active")
        self.assertEqual((await observer.frame())[0], "observation_end")
        await observer.close()

    async def test_omitted_agent_resolves_owner_and_stream_ends_before_next_run(self):
        hub = self.ctx.session_live
        hub.begin(self.key, "active")
        hub.publish(self.key, "active", "text", {"delta": "prefix"})
        observer = self._request(query={"run_id": "active"})
        headers = await observer.started()
        self.assertEqual(headers["status"], 200)
        self.assertIn((b"cache-control", b"no-store"), headers["headers"])
        sync = await observer.frame()
        self.assertEqual((sync[0], sync[1]["agent_id"]), ("observation_sync", self.agent_id))
        self.assertEqual((await observer.frame())[1]["data"]["delta"], "prefix")
        hub.end(self.key, "active")
        hub.begin(self.key, "next")
        hub.publish(self.key, "next", "text", {"delta": "must-not-arrive"})
        self.assertEqual(await observer.frame(), ("observation_end", {"run_id": "active"}))
        await asyncio.wait_for(observer.task, 5)
        self.assertEqual(hub._subscribers, {})

    async def test_generator_dispatch_race_and_preiteration_disconnect_do_not_leak(self):
        hub = self.ctx.session_live
        hub.begin(self.key, "active")
        request = Request({"type": "http", "scheme": "http", "server": ("test", 80), "headers": []})
        response = await observe_session_run(request, self.ctx, TEST_USER_ID, self.session, "active", self.agent_id)
        self.assertEqual(hub._subscribers, {})
        await response.body_iterator.aclose()
        self.assertEqual(hub._subscribers, {})
        response = await observe_session_run(request, self.ctx, TEST_USER_ID, self.session, "active", self.agent_id)
        hub.end(self.key, "active")
        hub.begin(self.key, "next")
        frames = [frame async for frame in response.body_iterator]
        self.assertEqual(len(frames), 1)
        self.assertEqual(frames[0]["event"], "observation_reset")
        self.assertEqual(hub._subscribers, {})

    async def test_producer_end_during_initial_replay_cannot_mix_next_run(self):
        hub = self.ctx.session_live
        hub.begin(self.key, "active", baseline_sequence=1)
        hub.publish(self.key, "active", "text", {"delta": "old-prefix"})
        request = Request({"type": "http", "scheme": "http", "server": ("test", 80), "headers": []})
        response = await observe_session_run(request, self.ctx, TEST_USER_ID, self.session, "active", self.agent_id)
        self.assertEqual((await anext(response.body_iterator))["event"], "observation_sync")
        hub.end(self.key, "active")
        hub.begin(self.key, "next")
        hub.publish(self.key, "next", "text", {"delta": "new-private-prefix"})
        frames = [frame async for frame in response.body_iterator]
        self.assertEqual([frame["event"] for frame in frames], ["observation", "observation_end"])
        self.assertEqual(json.loads(frames[0]["data"])["data"]["delta"], "old-prefix")
        self.assertNotIn("new-private-prefix", str(frames))
        self.assertEqual(hub._subscribers, {})

    async def _terminal_runner(self, *, cancel, disconnect=False):
        ready, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def stream(messages, info):
            nonlocal calls
            calls += 1
            yield "PARTIAL_BEFORE_TERMINAL"
            yield "_MORE"
            ready.set()
            await release.wait()
            raise RuntimeError("synthetic provider failure")

        runner = AgentRunner(Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps), self.ctx.store,
                             UsageLimits(request_limit=3), active_runs=self.ctx.active_runs)
        with patch("deep_agent.api.v1.chat.build_runner_for_agent", new=AsyncMock(return_value=runner)), \
             patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock):
            producer = self._request("/api/v1/chat/stream", method="POST", body={
                "agent_id": self.agent_id, "session_id": self.session, "message": "synthetic terminal test"})
            self.assertEqual((await producer.started())["status"], 200)
            run_id = (await producer.frame())[1]["run_id"]
            await asyncio.wait_for(ready.wait(), 5)
            if disconnect:
                await producer.close()
            observer = self._request(query={"agent_id": self.agent_id, "run_id": run_id})
            self.assertEqual((await observer.started())["status"], 200)
            self.assertEqual((await observer.frame())[0], "observation_sync")
            if cancel:
                response = await self.http.post(f"/api/v1/sessions/{self.session}/cancel", params={"agent_id": self.agent_id})
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["status"], "cancel_requested")
            else:
                release.set()
            frames = await observer.until("observation_end")
            expected = "cancelled" if cancel else "error"
            terminal = [data for kind, data in frames if kind == "observation" and data["event"] == expected]
            self.assertEqual(len(terminal), 1)
            self.assertFalse(any(kind == "observation" and data["event"] == "final" for kind, data in frames))
            await asyncio.wait_for(producer.task, 5)
            await asyncio.wait_for(observer.task, 5)
            self.assertEqual(calls, 1)
            self.assertEqual(self.ctx.active_runs.active_count(), 0)
            self.assertEqual(self.ctx.session_live._subscribers, {})
            status = await self.http.get(f"/api/v1/sessions/{self.session}/run-status", params={"agent_id": self.agent_id})
            self.assertEqual(status.json()["status"], "idle")
            rows = (await self.http.get(f"/api/v1/sessions/{self.session}/events", params={"agent_id": self.agent_id})).json()["items"]
            self.assertEqual(len([row for row in rows if row["event_type"] == "execution/error"]), 0 if cancel else 1)
            self.assertFalse(any(row["event_type"].startswith(("run/", "observation")) for row in rows))

    async def test_explicit_stop_terminates_observation_but_not_as_execution_error(self):
        rows = []
        with patch("deep_agent.diagnostics.emit", side_effect=lambda event, **kw: rows.append((event, kw))):
            await self._terminal_runner(cancel=True)
        for name in ("agent_cancel_api_received", "agent_cancel_observed", "agent_run_cancelled"):
            matches = [fields for event, fields in rows if event == name]
            self.assertEqual(len(matches), 1, name)
            self.assertEqual(matches[0]["reason"], "cancel_api")
            self.assertTrue(matches[0]["run_id"])

    async def test_sending_connection_disconnect_logs_source_and_preserves_history(self):
        await self._detached_execution("http_disconnect")

    async def test_cancel_original_run_after_sending_connection_disconnects(self):
        rows = []
        with patch("deep_agent.diagnostics.emit", side_effect=lambda event, **kw: rows.append((event, kw))):
            await self._terminal_runner(cancel=True, disconnect=True)
        self.assertEqual([fields["reason"] for event, fields in rows if event == "agent_cancel_observed"], ["cancel_api"])

    async def test_shutdown_cancellation_logs_source_and_preserves_history(self):
        await self._diagnostic_interruption("server_shutdown")

    async def test_unattributed_task_cancellation_is_not_a_client_stop(self):
        await self._detached_execution("task_cancelled_unknown")

    async def test_disconnect_with_broken_diagnostics_preserves_history(self):
        await self._detached_execution("http_disconnect", broken_sink=True)

    async def _detached_execution(self, reason, *, broken_sink=False):
        ready, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def stream(messages, info):
            nonlocal calls
            calls += 1
            yield "SYNTHETIC_PREFIX"
            yield "_BEFORE"
            ready.set()
            await release.wait()
            yield "_AFTER"

        runner = AgentRunner(Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps), self.ctx.store,
                             UsageLimits(request_limit=3), active_runs=self.ctx.active_runs)
        rows = []

        def emit(event, **fields):
            if broken_sink:
                raise RuntimeError("synthetic diagnostic failure")
            rows.append((event, fields))

        path = f"/api/v1/sessions/{self.session}"
        params = {"agent_id": self.agent_id}
        with patch("deep_agent.api.v1.chat.build_runner_for_agent", new=AsyncMock(return_value=runner)), \
             patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock), \
             patch("deep_agent.diagnostics.emit", side_effect=emit):
            producer = self._request("/api/v1/chat/stream", method="POST", body={
                **params, "session_id": self.session, "message": "synthetic disconnect"})
            self.assertEqual((await producer.started())["status"], 200)
            run_id = (await producer.frame())[1]["run_id"]
            await asyncio.wait_for(ready.wait(), 5)
            await producer.until("text")
            if reason == "task_cancelled_unknown":
                producer.task.cancel()
                await asyncio.wait_for(asyncio.gather(producer.task, return_exceptions=True), 5)
                self.requests.remove(producer)
            else:
                await producer.close()
            current = (await self.http.get(path + "/run-status", params=params)).json()
            self.assertEqual((current["status"], current["run_id"]), ("running", run_id))
            observer = self._request(query={**params, "run_id": run_id})
            self.assertEqual((await observer.started())["status"], 200)
            self.assertEqual((await observer.frame())[0], "observation_sync")
            release.set()
            frames = await observer.until("observation_end")
            await observer.close()
            final, = [data["data"] for kind, data in frames if kind == "observation" and data["event"] == "final"]
            self.assertEqual((final["run_id"], final["status"], final["output"]),
                             (run_id, "completed", "SYNTHETIC_PREFIX_BEFORE_AFTER"))
        self.assertEqual(calls, 1)
        if not broken_sink:
            closed, = [fields for event, fields in rows if event == "agent_stream_closed"]
            self.assertEqual((closed["reason"], closed["run_id"]), (reason, run_id))
            self.assertFalse(any(event in {"agent_cancel_observed", "agent_run_cancelled"} for event, _ in rows))
        self.assertEqual((await self.http.get(path + "/run-status", params=params)).json()["status"], "idle")
        items = (await self.http.get(path + "/history", params=params)).json()["items"]
        prompts = [item for item in items if item["kind"] == "user_message" and item["text"] == "synthetic disconnect"]
        self.assertEqual(len(prompts), 1)
        answer, = [item for item in items if item["kind"] == "assistant_message"]
        self.assertEqual((answer["run_id"], answer["status"], answer["text"]),
                         (run_id, "completed", final["output"]))

    async def _diagnostic_interruption(self, reason):
        ready = asyncio.Event()

        async def stream(messages, info):
            yield "SYNTHETIC_PARTIAL"
            yield "_MORE"
            ready.set()
            await asyncio.Event().wait()

        runner = AgentRunner(Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps), self.ctx.store,
                             UsageLimits(request_limit=3), active_runs=self.ctx.active_runs)
        rows = []

        def emit(event, **fields):
            rows.append((event, fields))

        with patch("deep_agent.api.v1.chat.build_runner_for_agent", new=AsyncMock(return_value=runner)), \
             patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock), \
             patch("deep_agent.diagnostics.emit", side_effect=emit):
            producer = self._request("/api/v1/chat/stream", method="POST", body={
                "agent_id": self.agent_id, "session_id": self.session, "message": "synthetic disconnect"})
            self.assertEqual((await producer.started())["status"], 200)
            run_id = (await producer.frame())[1]["run_id"]
            await asyncio.wait_for(ready.wait(), 5)
            self.ctx.active_runs.cancel_all()
            await asyncio.wait_for(producer.task, 5)
        for name in ("agent_cancel_observed", "agent_run_cancelled", "agent_stream_closed"):
            matches = [fields for event, fields in rows if event == name]
            self.assertEqual(len(matches), 1, name)
            expected = "stream_completed" if name == "agent_stream_closed" else reason
            self.assertEqual(matches[0]["reason"], expected)
            self.assertEqual(matches[0]["run_id"], run_id)
        self.assertFalse(any(event == "agent_cancel_api_received" for event, _ in rows))
        self.assertEqual(self.ctx.active_runs.active_count(), 0)
        events = await self.ctx.store.list_all_events(self.session)
        self.assertTrue(any(event.status == "interrupted" for event in events))
        self.assertFalse(any(event.event_type == "execution/error" for event in events))

    async def test_provider_error_is_forwarded_once_then_observation_closes(self):
        await self._terminal_runner(cancel=False)

    async def test_deferred_ui_observer_waits_for_pending_and_explicit_resume(self):
        ready, release = asyncio.Event(), asyncio.Event()
        calls = 0
        release_title = asyncio.Event()

        async def stream(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield "NEED_CONFIRMATION"
                yield {0: DeltaToolCall(name="builtin__confirm", json_args='{"message":"Synthetic confirmation"}',
                                       tool_call_id="confirm-once")}
                ready.set()
                await release.wait()
            else:
                yield "RESUMED_ONCE"

        async def generate_title(**kwargs):
            await release_title.wait()
            return TitleGenerationResult("Confirmation title", False)

        runner = AgentRunner(
            Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps,
                  toolsets=[build_external_toolset(builtin_ui_records())]),
            self.ctx.store, UsageLimits(request_limit=3), active_runs=self.ctx.active_runs,
            ui_tool_hints={"builtin__confirm": {"widget": "confirm"}},
        )
        with patch("deep_agent.api.v1.chat.build_runner_for_agent", new=AsyncMock(return_value=runner)) as build, \
             patch("deep_agent.agent.title_generator.generate_title", side_effect=generate_title):
            producer = self._request("/api/v1/chat/stream", method="POST", body={
                "agent_id": self.agent_id, "session_id": self.session, "message": "synthetic UI test"})
            self.assertEqual((await producer.started())["status"], 200)
            run_id = (await producer.frame())[1]["run_id"]
            await asyncio.wait_for(ready.wait(), 5)
            observer = self._request(query={"agent_id": self.agent_id, "run_id": run_id})
            self.assertEqual((await observer.started())["status"], 200)
            self.assertEqual((await observer.frame())[0], "observation_sync")
            before = (await self.http.get(f"/api/v1/sessions/{self.session}/history",
                                         params={"agent_id": self.agent_id})).json()
            self.assertFalse(any(item["status"] == "awaiting_input" for item in before["items"]))
            self.assertEqual(calls, 1)
            build.assert_awaited_once()
            release.set()
            # Ensure the pending tool call is already persisted while the title
            # is delayed. The producer must publish the title before the card.
            try:
                async with asyncio.timeout(5):
                    while True:
                        kind, data = await producer.frame()
                        self.assertNotIn(kind, {"tool_call", "final"})
                        if (kind == "session_event_appended" and data["event"]["event_type"] == "assistant/message"
                                and data["event"]["status"] == "pending"):
                            break
            finally:
                release_title.set()
            frames = await observer.until("observation_end")
            observed = [data for kind, data in frames if kind == "observation"]
            ui_calls = [data for data in observed if data["event"] == "tool_call"]
            self.assertEqual(len(ui_calls), 1)
            self.assertEqual(ui_calls[0]["data"]["tool_call_id"], "confirm-once")
            final = next(data for data in observed if data["event"] == "final")
            self.assertEqual(final["data"]["status"], "pending")
            model_title = next(d for d in observed if d["event"] == "session_title" and d["data"]["source"] == "model")
            self.assertLess(observed.index(model_title), observed.index(ui_calls[0]))
            self.assertLess(observed.index(ui_calls[0]), observed.index(final))
            self.assertEqual(frames[-1], ("observation_end", {"run_id": run_id}))
            await asyncio.wait_for(producer.task, 5)
            await asyncio.wait_for(observer.task, 5)
            self.assertEqual(self.ctx.active_runs.active_count(), 0)
            self.assertEqual(self.ctx.session_live._subscribers, {})
            pending = (await self.http.get(f"/api/v1/sessions/{self.session}/history",
                                          params={"agent_id": self.agent_id})).json()
            tools = [item for item in pending["items"] if item["kind"] == "tool"]
            self.assertEqual([(item["call_id"], item["status"]) for item in tools], [("confirm-once", "awaiting_input")])
            self.assertEqual(calls, 1, "Observation must not resume or start the Agent")
            # Only an explicit user result starts a new execution; the old
            # observer remains ended and the pending history supplies the call.
            resume = self._request("/api/v1/chat/stream", method="POST", body={
                "agent_id": self.agent_id, "session_id": self.session,
                "deferred_tool_results": [{"tool_call_id": "confirm-once", "result": {"confirmed": True}}]})
            self.assertEqual((await resume.started())["status"], 200)
            resume_id = (await resume.frame())[1]["run_id"]
            self.assertNotEqual(resume_id, run_id)
            resumed_frames = await resume.until("final")
            self.assertEqual(resumed_frames[-1][1]["output"], "RESUMED_ONCE")
            self.assertEqual(resumed_frames[-1][1]["status"], "completed")
            await asyncio.wait_for(resume.task, 5)
            self.assertEqual((calls, build.await_count), (2, 2))
            final_history = (await self.http.get(f"/api/v1/sessions/{self.session}/history",
                                                params={"agent_id": self.agent_id})).json()
            tools = [item for item in final_history["items"] if item["kind"] == "tool"]
            self.assertEqual(len(tools), 1)
            self.assertEqual(tools[0]["status"], "completed")
            self.assertEqual(tools[0]["output"], {"confirmed": True})

    async def test_real_runner_partial_text_fans_out_once_without_database_snapshot(self):
        ready, release = asyncio.Event(), asyncio.Event()
        calls = 0

        async def read_value() -> str:
            return "LIVE_TOOL_RESULT"

        async def stream(messages, info):
            nonlocal calls
            calls += 1
            if calls == 1:
                yield {0: DeltaToolCall(name="read_value", json_args="{}", tool_call_id="read-once")}
                return
            yield "LIVE_PREFIX"
            yield "_MIDDLE"
            ready.set()
            await release.wait()
            yield "_FINAL"

        tool = Tool(read_value, metadata={READ_ONLY_TOOL: True})
        runner = AgentRunner(Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps, tools=[tool]), self.ctx.store,
                             UsageLimits(request_limit=3), active_runs=self.ctx.active_runs)
        with patch("deep_agent.api.v1.chat.build_runner_for_agent", new=AsyncMock(return_value=runner)) as build, \
             patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock):
            producer = self._request("/api/v1/chat/stream", method="POST", body={
                "agent_id": self.agent_id, "session_id": self.session, "message": "synthetic live test"})
            self.assertEqual((await producer.started())["status"], 200)
            session_event = await producer.frame()
            self.assertEqual(session_event[0], "session")
            run_id = session_event[1]["run_id"]
            await asyncio.wait_for(ready.wait(), 5)
            observers = [self._request(query={"agent_id": self.agent_id, "run_id": run_id}) for _ in range(2)]
            for observer in observers:
                self.assertEqual((await observer.started())["status"], 200)
                sync = await observer.frame()
                self.assertEqual(sync[0], "observation_sync")
                self.assertEqual(sync[1]["baseline_sequence"], 1)
            # A completed round is not yet available in the isolated SQL history,
            # but both independent HTTP streams already show its partial text.
            prefixes = []
            for observer in observers:
                parts = []
                results = []
                while "LIVE_PREFIX" not in "".join(parts):
                    kind, envelope = await observer.frame()
                    self.assertEqual(kind, "observation")
                    if envelope["event"] == "text":
                        parts.append(envelope["data"].get("delta", envelope["data"].get("content", "")))
                    elif envelope["event"] == "tool_result":
                        results.append(envelope["data"]["tool_call_id"])
                prefixes.append("".join(parts))
                self.assertEqual(results, ["read-once"])
            self.assertEqual(prefixes[0], prefixes[1])
            history = await self.http.get(f"/api/v1/sessions/{self.session}/history", params={"agent_id": self.agent_id})
            self.assertEqual(history.status_code, 200, history.text)
            self.assertFalse(any(item["kind"] in {"assistant_message", "tool"} for item in history.json()["items"]))
            await observers[0].close()
            self.assertEqual(self.ctx.active_runs.active_count(), 1)
            self.assertFalse(producer.task.done())
            release.set()
            remaining = await observers[1].until("observation_end")
            finals = [data["data"] for kind, data in remaining if kind == "observation" and data["event"] == "final"]
            self.assertEqual(len(finals), 1)
            self.assertEqual(finals[0]["output"], "LIVE_PREFIX_MIDDLE_FINAL")
            await asyncio.wait_for(producer.task, 5)
            await asyncio.wait_for(observers[1].task, 5)
            self.assertEqual(calls, 2)
            build.assert_awaited_once()
            self.assertEqual(self.ctx.active_runs.active_count(), 0)
            self.assertEqual(self.ctx.session_live._runs, {})
            self.assertEqual(self.ctx.session_live._subscribers, {})
            history = (await self.http.get(f"/api/v1/sessions/{self.session}/history", params={"agent_id": self.agent_id})).json()
            self.assertEqual([item["text"] for item in history["items"] if item["kind"] == "assistant_message"], ["LIVE_PREFIX_MIDDLE_FINAL"])
            self.assertEqual(
                [item["output"] for item in history["items"] if item["kind"] == "tool"], ["LIVE_TOOL_RESULT"],
            )
            rows = (await self.http.get(f"/api/v1/sessions/{self.session}/events", params={"agent_id": self.agent_id})).json()["items"]
            self.assertFalse(any(row["event_type"].startswith(("run/", "observation")) for row in rows))

    async def test_producer_disconnect_keeps_buffered_read_result_in_history(self):
        ready, release = asyncio.Event(), asyncio.Event()

        async def read_value() -> str:
            return "SAVED_ON_DISCONNECT"

        async def stream(messages, info):
            if not ready.is_set() and not any(
                getattr(part, "tool_call_id", None) == "read-once"
                for message in messages for part in message.parts
            ):
                yield {0: DeltaToolCall(name="read_value", json_args="{}", tool_call_id="read-once")}
                return
            ready.set()
            await release.wait()
            yield "已复核"

        agent = Agent(
            FunctionModel(stream_function=stream), deps_type=AgentDeps,
            tools=[Tool(read_value, metadata={READ_ONLY_TOOL: True})],
        )
        runner = AgentRunner(agent, self.ctx.store, UsageLimits(request_limit=3), active_runs=self.ctx.active_runs)
        with patch("deep_agent.api.v1.chat.build_runner_for_agent", new=AsyncMock(return_value=runner)), \
             patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock):
            producer = self._request("/api/v1/chat/stream", method="POST", body={
                "agent_id": self.agent_id, "session_id": self.session, "message": "synthetic interrupted run",
            })
            self.assertEqual((await producer.started())["status"], 200)
            await asyncio.wait_for(ready.wait(), 5)
            history_url = f"/api/v1/sessions/{self.session}/history"
            params = {"agent_id": self.agent_id}
            during = (await self.http.get(history_url, params=params)).json()["items"]
            self.assertFalse(any(item["kind"] == "tool" for item in during))
            active = self.ctx.active_runs.get(agent_id=self.agent_id, session_id=self.session)
            self.assertIsNotNone(active)
            await producer.close()
            self.assertFalse(active.cancellation_token.cancelled)
            release.set()
            await asyncio.wait_for(active.finished.wait(), 5)
        after = (await self.http.get(history_url, params=params)).json()["items"]
        self.assertEqual([item["output"] for item in after if item["kind"] == "tool"], ["SAVED_ON_DISCONNECT"])
        self.assertEqual([item["text"] for item in after if item["kind"] == "assistant_message"], ["已复核"])
        self.assertEqual(self.ctx.active_runs.active_count(), 0)

    async def test_stop_after_compaction_persists_buffered_results(self):
        await self._interrupt_after_compaction("stop")

    async def test_disconnect_after_compaction_persists_buffered_results(self):
        await self._interrupt_after_compaction("disconnect")

    async def test_provider_failure_after_compaction_persists_buffered_results(self):
        await self._interrupt_after_compaction("provider_failure")

    async def test_shutdown_after_compaction_persists_buffered_results(self):
        await self._interrupt_after_compaction("shutdown")

    async def test_approval_after_compaction_persists_and_resumes_once(self):
        await self._interrupt_after_compaction("approval")

    async def _interrupt_after_compaction(self, ending):
        ready, release = asyncio.Event(), asyncio.Event()
        old_input = "历史资料" * 1000
        results = ["本轮新证据" * 2320, "复核值 128"]
        await self.ctx.store.append(self.session, [
            ModelRequest(parts=[UserPromptPart(old_input)]),
            ModelResponse(parts=[TextPart("历史结论" * 1000)]),
        ], agent_id=self.agent_id, user_id=TEST_USER_ID, run_id="prior-data")
        calls = summaries = requests = 0
        recording = False
        statements = []

        def record(_conn, _cursor, statement, *_):
            if recording:
                statements.append(statement)

        async def lookup() -> str:
            nonlocal calls
            calls += 1
            return results[calls - 1]

        def summarize(messages, info):
            nonlocal summaries
            summaries += 1
            return ModelResponse(parts=[TextPart(valid_summary())])

        async def stream(messages, info):
            nonlocal requests, recording
            requests += 1
            if requests > 1:
                self.assertEqual(len(summary_contents(messages)), 1)
                self.assertFalse(any(part.content == old_input for m in messages for part in m.parts
                                     if isinstance(part, UserPromptPart)))
            if requests <= 2:
                recording = requests == 2
                yield {0: DeltaToolCall(name="lookup", json_args="{}", tool_call_id=f"read-{requests}")}
                return
            recording = False
            returns = {p.tool_call_id: p.content for m in messages for p in m.parts if isinstance(p, ToolReturnPart)}
            self.assertEqual(returns["read-2"], results[1])
            if requests == 3:
                if ending == "approval":
                    yield {0: DeltaToolCall(name="builtin__confirm", json_args='{"message":"确认结果？"}',
                                            tool_call_id="approval")}
                    return
                yield "正在复核"
                ready.set()
                await release.wait()
                if ending == "disconnect":
                    yield "完成。"
                    return
                raise RuntimeError("synthetic provider interruption")
            if ending == "approval":
                self.assertEqual(returns["approval"], {"confirmed": True})
            yield results[1]

        capability = DeepAgentContextCompaction(context_window_tokens=16000, max_output_tokens=1000)
        model = FunctionModel(summarize, stream_function=stream)
        agent = Agent(model, deps_type=AgentDeps, tools=[Tool(lookup, metadata={READ_ONLY_TOOL: True})],
                      toolsets=[build_external_toolset(builtin_ui_records())] if ending == "approval" else [],
                      capabilities=[capability])
        runner = AgentRunner(agent, self.ctx.store, UsageLimits(request_limit=8), active_runs=self.ctx.active_runs,
                             context_compaction=capability, context_window_tokens=16000, max_output_tokens=1000)
        engine = self.ctx.db_engine.sync_engine
        sql_event.listen(engine, "before_cursor_execute", record)
        self.addCleanup(sql_event.remove, engine, "before_cursor_execute", record)
        params = {"agent_id": self.agent_id}
        body = {**params, "session_id": self.session}
        path = f"/api/v1/sessions/{self.session}"
        with patch("deep_agent.api.v1.chat.build_runner_for_agent", new=AsyncMock(return_value=runner)), \
             patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock):
            producer = self._request("/api/v1/chat/stream", method="POST", body={**body, "message": "核对本期"})
            self.assertEqual((await producer.started())["status"], 200)
            followup = {"message": "沿用刚才的复核值"}
            if ending == "approval":
                final = (await producer.until("final"))[-1][1]
                self.assertEqual(final["status"], "pending")
                followup = {"deferred_tool_results": [{"tool_call_id": "approval", "result": {"confirmed": True}}]}
            else:
                await asyncio.wait_for(ready.wait(), 5)
                await producer.until("text")
                if ending == "disconnect":
                    active = self.ctx.active_runs.get(agent_id=self.agent_id, session_id=self.session)
                    self.assertIsNotNone(active)
                    await producer.close()
                    self.assertFalse(active.cancellation_token.cancelled)
                    release.set()
                    await asyncio.wait_for(active.finished.wait(), 5)
                elif ending == "stop":
                    stop = await self.http.post(path + "/cancel", params=params)
                    self.assertEqual(stop.json()["status"], "cancel_requested")
                    self.assertTrue((await producer.until("cancelled"))[-1][1]["history_persisted"])
                elif ending == "shutdown":
                    self.ctx.active_runs.cancel_all()
                else:
                    release.set()
                    await producer.until("error")
            await asyncio.wait_for(producer.task, 5)
            self.assertEqual(statements, [], "Ordinary post-compaction tool round must execute zero SQL")
            self.assertEqual(self.ctx.active_runs.active_count(), 0)
            reopened = await self.http.get(path + "/history", params=params)
            self.assertEqual(reopened.status_code, 200)
            tools = [item for item in reopened.json()["items"] if item["kind"] == "tool"]
            self.assertEqual([(t["call_id"], t["output"]) for t in tools[:2]], list(zip(("read-1", "read-2"), results)))
            if ending == "approval":
                self.assertEqual([(t["call_id"], t["status"]) for t in tools[2:]], [("approval", "awaiting_input")])
            else:
                self.assertEqual(len(tools), 2)
                expected_text = "正在复核完成。" if ending == "disconnect" else "正在复核"
                partial = [item for item in reopened.json()["items"]
                           if item["kind"] == "assistant_message" and item["text"] == expected_text]
                self.assertEqual(len(partial), 1)
            events = await self.ctx.store.list_all_events(self.session)
            self.assertEqual([e.status for e in events if e.event_type == "compaction"], ["completed"])
            self.assertEqual(sum(e.event_type == "execution/error" for e in events), int(ending == "provider_failure"))
            resumed = self._request("/api/v1/chat/stream", method="POST", body={**body, **followup})
            self.assertEqual((await resumed.started())["status"], 200)
            final = (await resumed.until("final"))[-1][1]
            self.assertEqual((final["status"], final["output"]), ("completed", results[1]))
            await asyncio.wait_for(resumed.task, 5)
        self.assertEqual((requests, calls, summaries), (4, 2, 1))
