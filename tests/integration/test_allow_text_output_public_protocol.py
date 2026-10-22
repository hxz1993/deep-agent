"""Mixed output policy through actual public APIs, SQLite and the SDK.

Only the Provider and enterprise-account authority are deterministic fixtures.
No inherited TestCase collection or production protocol/persistence mocks.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from httpx import AsyncClient
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage

from deep_agent.agent.runner import AgentRunner
from deep_agent.channels.gateway.deepagent_client import StreamEvent
from deep_agent.security import ConsoleAdminAuthorizer
from tests.helpers.request_identity import TEST_USER_ID
from tests.integration import test_session_live_api as _live_fixture
from tests.integration import test_structured_output_public_protocol as _public_fixture
from tests.unit import test_channel_gateway as _channel_fixture


OBJECT_SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}},
                 "required": ["ok"], "additionalProperties": False}


def _cases():
    return [
        ("text", OBJECT_SCHEMA, [[TextPart("请先上传合同🧾。")]], "请先上传合同🧾。", False, [], 0),
        ("json", OBJECT_SCHEMA, [[ToolCallPart("public_result", {"ok": True}, "json")]], {"ok": True}, True, [], 0),
        ("string", {"type": "string"}, [[ToolCallPart("public_result", {"response": "same string"}, "string")]], "same string", True, [], 0),
        ("text-string", {"type": "string"}, [[TextPart("same string")]], "same string", False, [], 0),
        ("failed-json-then-text", OBJECT_SCHEMA,
         [[ToolCallPart("public_result", {}, "bad")], [TextPart("还需确认信息。")]], "还需确认信息。", False, [], 1),
        ("text-and-json", OBJECT_SCHEMA,
         [[TextPart("结果如下。"), ToolCallPart("public_result", {"ok": True}, "combined")]],
         {"ok": True}, True, ["结果如下。"], 0),
    ]


class AllowTextOutputPublicProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await _public_fixture.StructuredOutputPublicProtocolTests.asyncSetUp(self)
        self.ctx.console_admin_authorizer = ConsoleAdminAuthorizer(AsyncMock(return_value=True))

    asyncTearDown = _public_fixture.StructuredOutputPublicProtocolTests.asyncTearDown
    post = _public_fixture.StructuredOutputPublicProtocolTests.post
    stream = _public_fixture.StructuredOutputPublicProtocolTests.stream
    history = _public_fixture.StructuredOutputPublicProtocolTests.history
    events = _public_fixture.StructuredOutputPublicProtocolTests.events

    async def configure(self, schema):
        # Exercise the new management API, rather than bypassing policy storage.
        definition = {"name": "public_result", "description": "Synthetic mixed-policy API fixture", "json_schema": schema}
        headers = {"user": TEST_USER_ID, "authorization": "Bearer synthetic-admin"}
        async with AsyncClient(transport=self._transport, base_url="http://test") as http:
            changed = await http.patch(f"/api/v1/agents/{self._agent_id}", headers=headers, json={
                "structured_output_enabled": True, "allow_text_output": True, "output_config": definition})
            self.assertEqual(changed.status_code, 200, changed.text)
            readback = await http.get(f"/api/v1/agents/{self._agent_id}/output-config", headers=headers)
            self.assertEqual(readback.status_code, 200, readback.text)
            self.assertEqual(readback.json(), {"structured_output_enabled": True, "allow_text_output": True, "output_config": definition})
            ordinary = await http.get(f"/api/v1/agents/{self._agent_id}")
            self.assertEqual(ordinary.status_code, 200, ordinary.text)
            self.assertTrue(ordinary.json()["allow_text_output"])
            self.assertNotIn("output_config", ordinary.json())

    def model(self, replies, *, ready=None, release=None):
        requests = []

        def response(messages, info):
            requests.append(info)
            self.assertTrue(info.allow_text_output)
            self.assertEqual([tool.name for tool in info.output_tools], ["public_result"])
            self.assertLessEqual(len(requests), len(replies), "unexpected additional model request")
            return ModelResponse(parts=deepcopy(replies[len(requests) - 1]),
                                 usage=RequestUsage(input_tokens=13, output_tokens=9))

        async def stream(messages, info):
            result = response(messages, info)
            if ready is not None and len(requests) == 1:
                ready.set()
                await release.wait()
            for index, part in enumerate(result.parts):
                if isinstance(part, TextPart):
                    for chunk in (part.content[:2], part.content[2:]):
                        if chunk:
                            yield chunk
                else:
                    yield {index: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id)}

        return FunctionModel(response, stream_function=stream), requests

    async def assert_saved_result(self, sid, final, *, value, structured, preface=(), failed=0):
        self.assertEqual(final["status"], "completed", final)
        self.assertEqual(final["output"], value)
        self.assertIs(type(final["output"]), type(value))
        if structured:
            self.assertTrue(final["output_info"]["output_id"])
            self.assertEqual(final["output_info"]["name"], "public_result")
        else:
            self.assertNotIn("output_info", final)

        saved = (await self.history(sid))["items"]
        outputs = [item for item in saved if item["kind"] == "structured_output"]
        texts = [item["text"] for item in saved if item["kind"] == "assistant_message" and item.get("text")]
        self.assertEqual(texts, list(preface) if structured else [value])
        self.assertFalse([item for item in saved if item["kind"] == "tool"])
        self.assertEqual(len(outputs), int(structured))
        raw = await self.events(sid)
        attempts = [item for item in raw if item["event_type"] == "output/attempt"]
        self.assertEqual([bool(item["payload"]["accepted"]) for item in attempts], [False] * failed + [True] * int(structured))
        self.assertFalse([item for item in raw if item["event_type"] == "execution/error"])
        snapshots = [(item.get("payload", {}).get("message", {}).get("metadata") or {}).get("deepagent_output_contract") for item in raw]
        self.assertTrue(any(snapshot and snapshot.get("allow_text_output") is True for snapshot in snapshots))
        if structured:
            self.assertEqual(outputs[0]["item_id"], final["output_info"]["output_id"])
            self.assertEqual(outputs[0]["output"], value)
            self.assertIs(type(outputs[0]["output"]), type(value))
            self.assertEqual(attempts[-1]["event_id"], outputs[0]["item_id"])
            self.assertEqual(attempts[-1]["payload"]["output"], value)
            source_ids = attempts[-1]["payload"]["source"]["event_ids"]
            self.assertTrue(source_ids)
            self.assertTrue(set(source_ids) <= {item["event_id"] for item in raw})
        return attempts

    @asynccontextmanager
    async def live_cleanup(self, gates):
        start = len(self._requests)
        try:
            yield
        finally:
            for gate in gates:
                gate.set()
            for request in reversed(self._requests[start:]):
                await request.close()

    async def test_actual_http_native_output_classification_and_persisted_readback(self):
        for name, schema, replies, value, structured, preface, failed in _cases():
            with self.subTest(case=name):
                await self.configure(schema)
                model, requests = self.model(replies)
                final = await self.post(model)
                await self.assert_saved_result(final["session_id"], final, value=value,
                                               structured=structured, preface=preface, failed=failed)
                self.assertEqual(len(requests), len(replies))

    async def test_actual_sse_text_json_string_retry_and_combination_share_history_and_event_facts(self):
        for name, schema, replies, value, structured, preface, failed in _cases():
            with self.subTest(case=name):
                await self.configure(schema)
                model, requests = self.model(replies)
                frames = await self.stream(model)
                finals = [frame for frame in frames if frame["type"] == "final"]
                self.assertEqual(len(finals), 1)
                final = finals[0]
                sid = frames[0]["session_id"]
                attempts = await self.assert_saved_result(sid, final, value=value,
                    structured=structured, preface=preface, failed=failed)
                public = [frame for frame in frames if frame["type"] == "output"]
                self.assertEqual([frame["accepted"] for frame in public], [False] * failed + [True] * int(structured))
                self.assertEqual([frame["output_id"] for frame in public], [item["event_id"] for item in attempts])
                text = "".join(frame.get("delta", "") for frame in frames if frame["type"] == "text")
                self.assertEqual(text, "".join(preface) if structured else value)
                self.assertFalse([frame for frame in frames if frame["type"] in {"tool_call", "tool_result", "error"}])
                for frame in public:
                    self.assertLess(frames.index(frame), frames.index(final))
                    self.assertTrue(any(prior["type"] == "session_event_appended"
                        and prior["event"]["event_id"] == frame["output_id"] for prior in frames[:frames.index(frame)]))
                    if not frame["accepted"]:
                        self.assertNotIn("output", frame)
                if structured:
                    self.assertEqual(public[-1]["output"], value)
                    self.assertEqual(public[-1]["output_id"], final["output_info"]["output_id"])
                self.assertEqual(len(requests), len(replies))

    async def test_mixed_policy_does_not_expand_owner_permissions_for_text_json_or_live(self):
        for structured in (False, True):
            with self.subTest(structured=structured):
                await self.configure({"type": "string"})
                value = "synthetic-owner-only"
                parts = [ToolCallPart("public_result", {"response": value}, "private")] if structured else [TextPart(value)]
                model, _ = self.model([parts])
                final = await self.post(model)
                async with AsyncClient(transport=self._transport, base_url="http://test", headers={"user": "different-owner"}) as http:
                    for suffix in ("history", "events", "live"):
                        denied = await http.get(f"/api/v1/sessions/{final['session_id']}/{suffix}",
                            params={"agent_id": self._agent_id, "run_id": final["run_id"]})
                        self.assertEqual(denied.status_code, 404, denied.text)
                        self.assertNotIn(value, denied.text)

    async def test_live_replay_disconnect_text_and_json_keep_final_kind_and_acceptance_identity(self):
        for name, schema, replies, value, structured, preface, failed in _cases():
            with self.subTest(case=name):
                await self.configure(schema)
                ready, release, settled, release_final = [asyncio.Event() for _ in range(4)]
                self._gates.extend([release, release_final])
                model, requests = self.model(replies, ready=ready, release=release)
                original = AgentRunner.run_stream
                finals = []

                async def gated(runner, *args, **kwargs):
                    async for event in original(runner, *args, **kwargs):
                        if event.get("type") == "final":
                            finals.append(event)
                            settled.set()
                            await release_final.wait()
                        yield event

                def request(path, **kwargs):
                    current = _live_fixture._StreamingRequest(self._app, path, **kwargs)
                    self._requests.append(current)
                    return current

                sid = uuid4().hex
                with patch("deep_agent.agent.factory._resolve_model", return_value=model), patch.object(AgentRunner, "run_stream", new=gated):
                    async with self.live_cleanup((release, release_final)):
                        producer = request("/api/v1/chat/stream", method="POST", body={"agent_id": self._agent_id,
                            "session_id": sid, "message": "Synthetic mixed-policy Live request", "capabilities": []})
                        self.assertEqual((await producer.started())["status"], 200)
                        kind, session = await producer.frame()
                        self.assertEqual(kind, "session")
                        run_id = session["run_id"]
                        await asyncio.wait_for(ready.wait(), 5)
                        path, query = f"/api/v1/sessions/{sid}/live", {"agent_id": self._agent_id, "run_id": run_id}
                        observers = [request(path, query=query) for _ in range(2)]
                        for observer in observers:
                            self.assertEqual((await observer.started())["status"], 200)
                            self.assertEqual((await observer.frame())[0], "observation_sync")
                        await observers[0].close()
                        self.assertEqual(self.ctx.active_runs.active_count(), 1)
                        self.assertFalse(producer.task.done())
                        release.set()
                        await asyncio.wait_for(settled.wait(), 5)
                        self.assertEqual(len(finals), 1)
                        await self.assert_saved_result(sid, finals[0], value=value,
                            structured=structured, preface=preface, failed=failed)
                        replay = request(path, query=query)
                        self.assertEqual((await replay.started())["status"], 200)
                        self.assertEqual((await replay.frame())[0], "observation_sync")
                        release_final.set()
                        observed = await observers[1].until("observation_end")
                        replayed = await replay.until("observation_end")
                        for capture in (observed, replayed):
                            envelopes = [envelope for kind, envelope in capture if kind == "observation"]
                            live_final = [env["data"] for env in envelopes if env["event"] == "final"]
                            self.assertEqual(len(live_final), 1)
                            self.assertEqual(live_final[0]["output"], value)
                            live_outputs = [env["data"] for env in envelopes if env["event"] == "output"]
                            self.assertEqual([frame["accepted"] for frame in live_outputs], [False] * failed + [True] * int(structured))
                            self.assertEqual(len({frame["output_id"] for frame in live_outputs}), len(live_outputs))
                            text = "".join(env["data"].get("delta", "") for env in envelopes if env["event"] == "text")
                            self.assertEqual(text, "".join(preface) if structured else value)
                            if structured:
                                self.assertEqual(live_final[0]["output_info"]["output_id"], finals[0]["output_info"]["output_id"])
                                self.assertEqual(live_outputs[-1]["output_id"], finals[0]["output_info"]["output_id"])
                            else:
                                self.assertNotIn("output_info", live_final[0])
                        await asyncio.wait_for(producer.task, 5)
                        self.assertEqual(len(requests), len(replies))
                        self.assertEqual(self.ctx.active_runs.active_count(), 0)
                        self.assertEqual(self.ctx.session_live._runs, {})
                        self.assertEqual(self.ctx.session_live._subscribers, {})


class AllowTextOutputChannelRoutingTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = _channel_fixture.ChannelGatewayTests.asyncSetUp
    asyncTearDown = _channel_fixture.ChannelGatewayTests.asyncTearDown
    _run_presentation = _channel_fixture.ChannelGatewayTests._run_presentation

    async def test_same_string_channel_delivery_uses_output_info_not_python_value_type(self):
        for structured in (False, True):
            with self.subTest(structured=structured):
                final = {"status": "completed", "output": "same string"}
                if structured:
                    final["output_info"] = {"name": "public_result", "output_id": "synthetic-output"}
                _, agent, adapter, row, _ = await self._run_presentation([
                    StreamEvent("final", final)], message_id=f"mixed-string-{structured}",
                    adapter=_channel_fixture.StructuredOutputChannelAdapter())
                delivered = [text for text, finish in adapter.replies if finish]
                self.assertEqual(len(delivered), 1)
                if structured:
                    self.assertEqual(delivered[0], 'public_result\n```json\n"same string"\n```')
                else:
                    self.assertEqual(delivered[0], "same string")
                self.assertEqual(row.processing_status, "completed")
                self.assertEqual(row.delivery_status, "sent")
                self.assertEqual(agent.calls, 1)

    async def test_failed_json_attempt_does_not_replace_subsequent_text_delivery(self):
        _, _, adapter, row, _ = await self._run_presentation([
            StreamEvent("output", {"accepted": False, "status": "error", "output": {"rejected": "candidate"}}),
            StreamEvent("text", {"delta": "还需确认。"}),
            StreamEvent("final", {"status": "completed", "output": "还需确认。"}),
        ], message_id="mixed-failed-json-text", adapter=_channel_fixture.StructuredOutputChannelAdapter())
        self.assertEqual([text for text, finish in adapter.replies if finish], ["还需确认。"])
        self.assertEqual(row.delivery_status, "sent")
        self.assertFalse(adapter.native_delivered)
