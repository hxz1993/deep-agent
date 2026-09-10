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

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.api.v1 import api_router
from deep_agent.api.v1.sessions import observe_session_run
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.capabilities.ui.registry import build_external_toolset
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.skills.runtime import SkillRuntimeLibrary
from tests.helpers.request_identity import TEST_USER_ID
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class _StreamingRequest:
    def __init__(self, app, path, *, method="GET", query=None, body=None):
        self.incoming, self.outgoing = asyncio.Queue(), asyncio.Queue()
        self.buffer = ""
        raw = json.dumps(body).encode() if body is not None else b""
        self.incoming.put_nowait({"type": "http.request", "body": raw, "more_body": False})
        scope = {"type": "http", "asgi": {"version": "3.0", "spec_version": "2.4"},
                 "http_version": "1.1", "method": method, "scheme": "http", "path": path,
                 "raw_path": path.encode(), "root_path": "", "query_string": urlencode(query or {}).encode(),
                 "headers": [(b"user", TEST_USER_ID.encode()), (b"content-type", b"application/json")],
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

    async def test_unavailable_replay_returns_409_instead_of_partial_prefix(self):
        self.ctx.session_live.max_replay_frames = 1
        self.ctx.session_live.begin(self.key, "active")
        for index in range(2):
            self.ctx.session_live.publish(self.key, "active", "text", {"delta": str(index)})
        response = await self.http.get(f"/api/v1/sessions/{self.session}/live", params={"run_id": "active"})
        self.assertEqual(response.status_code, 409)
        self.assertIn("replay", response.json()["detail"].lower())

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
        response = await observe_session_run(self.ctx, TEST_USER_ID, self.session, "active", self.agent_id)
        self.assertEqual(hub._subscribers, {})
        await response.body_iterator.aclose()
        self.assertEqual(hub._subscribers, {})
        response = await observe_session_run(self.ctx, TEST_USER_ID, self.session, "active", self.agent_id)
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
        response = await observe_session_run(self.ctx, TEST_USER_ID, self.session, "active", self.agent_id)
        self.assertEqual((await anext(response.body_iterator))["event"], "observation_sync")
        hub.end(self.key, "active")
        hub.begin(self.key, "next")
        hub.publish(self.key, "next", "text", {"delta": "new-private-prefix"})
        frames = [frame async for frame in response.body_iterator]
        self.assertEqual([frame["event"] for frame in frames], ["observation", "observation_end"])
        self.assertEqual(json.loads(frames[0]["data"])["data"]["delta"], "old-prefix")
        self.assertNotIn("new-private-prefix", str(frames))
        self.assertEqual(hub._subscribers, {})

    async def _terminal_runner(self, *, cancel):
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
        await self._diagnostic_interruption("http_disconnect")

    async def test_shutdown_cancellation_logs_source_and_preserves_history(self):
        await self._diagnostic_interruption("server_shutdown")

    async def test_unattributed_task_cancellation_is_not_a_client_stop(self):
        await self._diagnostic_interruption("task_cancelled_unknown")

    async def test_disconnect_with_broken_diagnostics_preserves_history(self):
        await self._diagnostic_interruption("http_disconnect", broken_sink=True)

    async def _diagnostic_interruption(self, reason, *, broken_sink=False):
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
            if broken_sink:
                raise RuntimeError("synthetic diagnostic failure")
            rows.append((event, fields))

        with patch("deep_agent.api.v1.chat.build_runner_for_agent", new=AsyncMock(return_value=runner)), \
             patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock), \
             patch("deep_agent.diagnostics.emit", side_effect=emit):
            producer = self._request("/api/v1/chat/stream", method="POST", body={
                "agent_id": self.agent_id, "session_id": self.session, "message": "synthetic disconnect"})
            self.assertEqual((await producer.started())["status"], 200)
            run_id = (await producer.frame())[1]["run_id"]
            await asyncio.wait_for(ready.wait(), 5)
            if reason == "server_shutdown":
                self.ctx.active_runs.cancel_all()
                await asyncio.wait_for(producer.task, 5)
            elif reason == "task_cancelled_unknown":
                producer.task.cancel()
                await asyncio.wait_for(asyncio.gather(producer.task, return_exceptions=True), 5)
                # Mark the already-closed transport for fixture teardown, which
                # otherwise re-raises the expected CancelledError.
                self.requests.remove(producer)
            else:
                await producer.close()
        for name in (() if broken_sink else ("agent_cancel_observed", "agent_run_cancelled", "agent_stream_closed")):
            matches = [fields for event, fields in rows if event == name]
            self.assertEqual(len(matches), 1, name)
            expected = "stream_completed" if reason == "server_shutdown" and name == "agent_stream_closed" else reason
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

        runner = AgentRunner(
            Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps,
                  toolsets=[build_external_toolset(builtin_ui_records())]),
            self.ctx.store, UsageLimits(request_limit=3), active_runs=self.ctx.active_runs,
            ui_tool_hints={"builtin__confirm": {"widget": "confirm"}},
        )
        with patch("deep_agent.api.v1.chat.build_runner_for_agent", new=AsyncMock(return_value=runner)) as build, \
             patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock):
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
            frames = await observer.until("observation_end")
            observed = [data for kind, data in frames if kind == "observation"]
            ui_calls = [data for data in observed if data["event"] == "tool_call"]
            self.assertEqual(len(ui_calls), 1)
            self.assertEqual(ui_calls[0]["data"]["tool_call_id"], "confirm-once")
            final = next(data for data in observed if data["event"] == "final")
            self.assertEqual(final["data"]["status"], "pending")
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

        async def stream(messages, info):
            nonlocal calls
            calls += 1
            yield "LIVE_PREFIX"
            yield "_MIDDLE"
            ready.set()
            await release.wait()
            yield "_FINAL"

        runner = AgentRunner(Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps), self.ctx.store,
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
                while "LIVE_PREFIX" not in "".join(parts):
                    kind, envelope = await observer.frame()
                    self.assertEqual(kind, "observation")
                    if envelope["event"] == "text":
                        parts.append(envelope["data"].get("delta", envelope["data"].get("content", "")))
                prefixes.append("".join(parts))
            self.assertEqual(prefixes[0], prefixes[1])
            history = await self.http.get(f"/api/v1/sessions/{self.session}/history", params={"agent_id": self.agent_id})
            self.assertEqual(history.status_code, 200, history.text)
            self.assertFalse(any(item["kind"] == "assistant_message" for item in history.json()["items"]))
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
            self.assertEqual(calls, 1)
            build.assert_awaited_once()
            self.assertEqual(self.ctx.active_runs.active_count(), 0)
            self.assertEqual(self.ctx.session_live._runs, {})
            self.assertEqual(self.ctx.session_live._subscribers, {})
            history = (await self.http.get(f"/api/v1/sessions/{self.session}/history", params={"agent_id": self.agent_id})).json()
            self.assertEqual([item["text"] for item in history["items"] if item["kind"] == "assistant_message"], ["LIVE_PREFIX_MIDDLE_FINAL"])
            rows = (await self.http.get(f"/api/v1/sessions/{self.session}/events", params={"agent_id": self.agent_id})).json()["items"]
            self.assertFalse(any(row["event_type"].startswith(("run/", "observation")) for row in rows))
