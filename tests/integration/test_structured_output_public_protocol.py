"""Real HTTP/SSE/Live/History APIs, real SQLite, deterministic SDK Provider."""
from __future__ import annotations

import asyncio
import json
import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from uuid import uuid4

from httpx import AsyncClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage

from deep_agent.agent.runner import AgentRunner
from deep_agent.core.context import get_context
from deep_agent.diagnostics.sessions import DiagnosticSessionQueries, SessionFilters, detail_event_metadata
from deep_agent.schemas.agent import AgentUpdate
from tests.helpers.request_identity import TEST_USER_ID
from tests.integration import test_chat_history_persistence as _history_fixture
from tests.integration import test_session_live_api as _live_fixture
from tests.integration.helpers.chat import mock_request_disconnect


class StructuredOutputPublicProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await _history_fixture.ChatHistoryPersistenceTests.asyncSetUp(self)
        self.ctx = get_context()
        self._requests, self._gates = [], []

    async def asyncTearDown(self):
        for gate in self._gates:
            gate.set()
        for request in reversed(self._requests):
            await request.close()
        await _history_fixture.ChatHistoryPersistenceTests.asyncTearDown(self)

    async def configure(self, schema):
        await self.ctx.stores.agent.update_unique(self._agent_id, AgentUpdate(
            structured_output_enabled=True,
            output_config={"name": "public_result", "description": "Synthetic API fixture", "json_schema": schema},
        ), user_id=TEST_USER_ID)

    def model(self, values, schema):
        calls = []
        def response(messages, info):
            calls.append(info)
            value = values[min(len(calls) - 1, len(values) - 1)]
            args = value if schema.get("type") == "object" else {"response": value}
            return ModelResponse(parts=[ToolCallPart("public_result", args, f"output-{len(calls)}")],
                                 usage=RequestUsage(input_tokens=11, output_tokens=7))
        async def stream(messages, info):
            result = response(messages, info)
            for index, part in enumerate(result.parts):
                yield {index: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id)}
        return FunctionModel(response, stream_function=stream), calls

    async def post(self, model):
        with patch("deep_agent.agent.factory._resolve_model", return_value=model), mock_request_disconnect():
            async with AsyncClient(transport=self._transport, base_url="http://test") as http:
                response = await http.post("/api/v1/chat", json={
                    "agent_id": self._agent_id, "message": "Synthetic API request", "capabilities": []})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def stream(self, model):
        return await _history_fixture.ChatHistoryPersistenceTests._stream_events(
            self, model=model, json_body={"message": "Synthetic SSE request"})

    async def history(self, sid):
        return await _history_fixture.ChatHistoryPersistenceTests._history(self, sid)

    async def events(self, sid):
        async with AsyncClient(transport=self._transport, base_url="http://test") as http:
            response = await http.get(f"/api/v1/sessions/{sid}/events", params={"agent_id": self._agent_id})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["items"]

    async def test_sync_http_native_roots_and_matching_history_identity(self):
        for value, schema in [({"text": "synthetic", "zero": 0, "disabled": False, "nullable": None}, {"type": "object"}),
                              ([{"id": "one"}], {"type": "array", "items": {"type": "object"}}),
                              (False, {"type": "boolean"}), (0, {"type": "integer"}),
                              (None, {"type": "null"}), ("", {"type": "string"})]:
            with self.subTest(value=value):
                await self.configure(schema)
                model, calls = self.model([value], schema)
                result = await self.post(model)
                self.assertEqual(result["output"], value)
                self.assertIs(type(result["output"]), type(value))
                self.assertEqual(len(calls), 1)
                outputs = [item for item in (await self.history(result["session_id"]))["items"] if item["kind"] == "structured_output"]
                self.assertEqual(len(outputs), 1)
                self.assertEqual(outputs[0]["item_id"], result["output_info"]["output_id"])
                self.assertEqual(outputs[0]["output"], value)

    async def test_disabled_http_and_sse_add_no_output_fields_or_facts(self):
        def plain(messages, info):
            self.assertEqual(info.output_tools, [])
            return ModelResponse(parts=[TextPart("unchanged plain text")])
        async def plain_stream(messages, info):
            self.assertEqual(info.output_tools, [])
            yield "unchanged plain text"
        model = FunctionModel(plain, stream_function=plain_stream)
        http, frames = await self.post(model), await self.stream(model)
        final = next(frame for frame in reversed(frames) if frame["type"] == "final")
        self.assertNotIn("output_info", http)
        self.assertNotIn("output_info", final)
        self.assertEqual(http["output"], final["output"])
        self.assertNotIn("output", [frame["type"] for frame in frames])
        for sid in [http["session_id"], frames[0]["session_id"]]:
            self.assertNotIn("structured_output", [item["kind"] for item in (await self.history(sid))["items"]])
            raw = await self.events(sid)
            self.assertFalse(any(item["event_type"] == "output/attempt" for item in raw))
            self.assertNotIn("deepagent_output_contract", json.dumps(raw))

    async def test_sse_failed_accepted_order_and_cross_channel_identity(self):
        schema = {"type": "object", "required": ["contract"], "properties": {
            "contract": {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}}}
        await self.configure(schema)
        value = {"contract": {"id": "synthetic"}}
        model, calls = self.model([{"contract": {}}, value], schema)
        frames = await self.stream(model)
        attempts = [frame for frame in frames if frame["type"] == "output"]
        self.assertEqual([frame["status"] for frame in attempts], ["error", "completed"])
        self.assertEqual([frame["accepted"] for frame in attempts], [False, True])
        self.assertNotIn("output", attempts[0])
        self.assertTrue(attempts[0]["errors"])
        self.assertEqual(attempts[1]["output"], value)
        final = next(frame for frame in reversed(frames) if frame["type"] == "final")
        self.assertLess(frames.index(attempts[0]), frames.index(attempts[1]))
        self.assertLess(frames.index(attempts[1]), frames.index(final))
        self.assertFalse(any(frame["type"] in {"tool_call", "tool_result", "error"} for frame in frames))
        self.assertEqual(len(calls), 2)
        self.assertEqual(final["output_info"]["output_id"], attempts[1]["output_id"])
        history = await self.history(frames[0]["session_id"])
        outputs = [item for item in history["items"] if item["kind"] == "structured_output"]
        self.assertEqual([item["item_id"] for item in outputs], [attempts[1]["output_id"]])
        self.assertFalse([item for item in history["items"] if item["kind"] == "tool"])
        persisted = [item for item in await self.events(frames[0]["session_id"]) if item["event_type"] == "output/attempt"]
        self.assertEqual([item["event_id"] for item in persisted], [frame["output_id"] for frame in attempts])
        for frame in attempts:
            self.assertTrue(any(item["type"] == "session_event_appended" and item["event"]["event_id"] == frame["output_id"] for item in frames[:frames.index(frame)]))

    async def test_corrections_do_not_duplicate_usage_questions_tools_or_recent_errors(self):
        schema = {"type": "object", "required": ["id"]}
        await self.configure(schema)
        model, calls = self.model([{}, {"id": "accepted"}], schema)
        result = await self.post(model)
        metadata = detail_event_metadata(await self.ctx.store.list_all_events(result["session_id"]))
        self.assertEqual(len(calls), 2)
        self.assertEqual(result["usage"]["requests"], 2)
        self.assertEqual(metadata["turn_count"], 1)
        self.assertEqual(metadata["tool_call_count"], 0)
        self.assertEqual(metadata["usage"]["input_tokens"], 22)
        self.assertEqual(metadata["usage"]["output_tokens"], 14)
        self.assertEqual(metadata["runs"][0]["recorded_model_responses"], 2)
        now = datetime.now(UTC)
        overview = await DiagnosticSessionQueries(self.ctx.db_session_factory).overview(SessionFilters(
            now - timedelta(minutes=5), now + timedelta(minutes=1), agent_id=self._agent_id))
        self.assertEqual(overview["metrics"]["question_count"], 1)
        self.assertEqual(overview["metrics"]["error_session_count"], 0)
        self.assertEqual(overview["recent_errors"], [])
        async with AsyncClient(transport=self._transport, base_url="http://test") as http:
            listing = await http.get("/api/v1/sessions", params={"agent_id": self._agent_id})
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()["items"][0]["turn_count"], 1)

    async def test_ordinary_history_events_are_owner_scoped(self):
        schema = {"type": "object"}
        await self.configure(schema)
        model, _ = self.model([{"secret": "synthetic-owner-only"}], schema)
        result = await self.post(model)
        async with AsyncClient(transport=self._transport, base_url="http://test", headers={"user": "other-user"}) as http:
            for suffix in ("history", "events"):
                response = await http.get(f"/api/v1/sessions/{result['session_id']}/{suffix}", params={"agent_id": self._agent_id})
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("synthetic-owner-only", response.text)

    async def test_live_replay_history_same_id_observer_disconnect_keeps_producer(self):
        await self.configure({"type": "object", "required": ["id"]})
        ready, release, accepted, release_final = [asyncio.Event() for _ in range(4)]
        self._gates.extend([release, release_final])
        calls, value = [], {"id": "synthetic-live", "nullable": None, "disabled": False}
        async def stream(messages, info):
            calls.append(info)
            ready.set()
            await release.wait()
            yield {0: DeltaToolCall(name="public_result", json_args=json.dumps(value), tool_call_id="live-output")}
        original = AgentRunner.run_stream
        async def gated(runner, *args, **kwargs):
            async for event in original(runner, *args, **kwargs):
                yield event
                if event.get("type") == "output" and event.get("accepted"):
                    accepted.set()
                    await release_final.wait()
        sid = uuid4().hex
        def request(path, **kwargs):
            item = _live_fixture._StreamingRequest(self._app, path, **kwargs)
            self._requests.append(item)
            return item
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(stream_function=stream)), patch.object(AgentRunner, "run_stream", new=gated):
            producer = request("/api/v1/chat/stream", method="POST", body={
                "agent_id": self._agent_id, "session_id": sid, "message": "Synthetic live output", "capabilities": []})
            self.assertEqual((await producer.started())["status"], 200)
            kind, session = await producer.frame()
            self.assertEqual(kind, "session")
            run_id = session["run_id"]
            await asyncio.wait_for(ready.wait(), 5)
            observers = [request(f"/api/v1/sessions/{sid}/live", query={"agent_id": self._agent_id, "run_id": run_id}) for _ in range(2)]
            for observer in observers:
                self.assertEqual((await observer.started())["status"], 200)
                self.assertEqual((await observer.frame())[0], "observation_sync")
            await observers[0].close()
            self.assertEqual(self.ctx.active_runs.active_count(), 1)
            self.assertFalse(producer.task.done())
            release.set()
            await asyncio.wait_for(accepted.wait(), 5)
            output = next(item for item in (await self.history(sid))["items"] if item["kind"] == "structured_output")
            replay = request(f"/api/v1/sessions/{sid}/live", query={"agent_id": self._agent_id, "run_id": run_id})
            self.assertEqual((await replay.started())["status"], 200)
            self.assertEqual((await replay.frame())[0], "observation_sync")
            replayed = []
            while not replayed:
                kind, envelope = await replay.frame()
                if kind == "observation" and envelope["event"] == "output":
                    replayed.append(envelope["data"])
            self.assertEqual(replayed[0]["output_id"], output["item_id"])
            self.assertEqual(replayed[0]["output"], value)
            release_final.set()
            observed = await observers[1].until("observation_end")
            remaining = await replay.until("observation_end")
            live = [env["data"] for kind, env in observed if kind == "observation" and env["event"] == "output"]
            self.assertEqual([item["output_id"] for item in live], [output["item_id"]])
            finals = [env["data"] for kind, env in remaining if kind == "observation" and env["event"] == "final"]
            self.assertEqual(finals[0]["output_info"]["output_id"], output["item_id"])
            self.assertEqual(len({output["item_id"], live[0]["output_id"], replayed[0]["output_id"], finals[0]["output_info"]["output_id"]}), 1)
            await asyncio.wait_for(producer.task, 5)
            self.assertEqual(len(calls), 1)
            self.assertEqual(self.ctx.active_runs.active_count(), 0)
            self.assertEqual(self.ctx.session_live._runs, {})
            self.assertEqual(self.ctx.session_live._subscribers, {})
