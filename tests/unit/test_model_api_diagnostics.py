from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch

import httpx2
from pydantic_ai import Agent
from pydantic_ai.capabilities import WebSearch
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
from pydantic_ai.providers.openai import OpenAIProvider

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.diagnostics import RuntimeDiagnostics
from deep_agent.agent.model_api_diagnostics import observe_model_api, _scope


class ModelApiDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_web_search_is_in_actual_wire_manifest(self):
        wire, rows = [], []

        def transport(request):
            wire.append(json.loads(request.content))
            return httpx2.Response(400, json={"error": {"message": "fixture stop"}})

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
            model = OpenAIResponsesModel("gpt-4.1", provider=OpenAIProvider(
                api_key="fixture-only", http_client=client))
            with patch("deep_agent.agent.model_api_diagnostics.log_event",
                       side_effect=lambda event, **kw: rows.append((event, kw))):
                agent = Agent(model, deps_type=AgentDeps,
                              capabilities=[RuntimeDiagnostics(), WebSearch(native=True, local=False)])
                with self.assertRaises(ModelHTTPError):
                    await agent.run("private fixture prompt", deps=AgentDeps(session_id="fixture"))
        self.assertEqual(wire[0]["tools"][0]["type"], "web_search")
        self.assertEqual(rows[0][1]["tool_names"], ["web_search"])
        self.assertEqual(rows[0][1]["tool_count"], 1)
        self.assertNotIn("private fixture prompt", repr(rows))

    async def test_actual_wire_duplicates_delta_endpoint_and_status_without_body(self):
        sent, rows = [], []

        async def transport(request):
            sent.append(request.content)
            return httpx2.Response(400, json={"error": {"message": "private failure"}})

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
            model = OpenAIChatModel("fixture", provider=OpenAIProvider(api_key="private-key", http_client=client))
            previous = {}
            with patch("deep_agent.agent.model_api_diagnostics.log_event", side_effect=lambda event, **kw: rows.append((event, kw))):
                for index, names in enumerate((["base"], ["base", "search", "search"]), 1):
                    body = {"messages": [{"content": "private prompt and image-data"}],
                            "input": [{"type": "tool_search_output", "tools": [{"name": "search", "parameters": {"private": "schema"}}]},
                                      {"type": "message", "content": "private history"}],
                            "tools": [{"type": "function", "function": {"name": name, "description": "private schema"}} for name in names]}
                    with observe_model_api(model, {"run_id": "run", "request_index": index}, previous):
                        response = await client.post("https://user:password@example.test/private-token/chat/completions?key=secret", json=body)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(json.loads(sent[-1]), body)
                # Hooks remain installed but emit nothing outside a model scope.
                await client.get("https://example.test/unrelated")
            self.assertEqual(len(client.event_hooks["request"]), 1)
            self.assertEqual(len(client.event_hooks["response"]), 1)
        self.assertEqual(len(rows), 4)
        second = rows[2][1]
        self.assertEqual(second["tool_names"], ["base", "search", "search"])
        self.assertEqual(second["duplicate_tool_names"], ["search"])
        self.assertEqual(second["added_tool_names"], ["search"])
        self.assertEqual(second["history_tool_names"], ["search"])
        self.assertEqual(second["redeclared_tool_names"], ["search"])
        self.assertEqual((second["tool_count"], second["unique_tool_count"]), (3, 2))
        self.assertEqual(second["model_api"], "https://example.test")
        self.assertEqual(second["api_path"], "/chat/completions")
        self.assertEqual(rows[3][1]["http_status"], 400)
        self.assertEqual(second["api_request_id"], rows[3][1]["api_request_id"])
        for secret in ("private", "password", "secret", "image-data"):
            self.assertNotIn(secret, repr(rows))
        self.assertIsNone(_scope.get())

    async def test_real_sdk_error_retains_identity_and_actual_api_status(self):
        rows = []
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda request: httpx2.Response(
            400, json={"error": {"message": "Tool names must be unique.", "type": "invalid_request_error"}}))) as client:
            model = OpenAIChatModel("fixture", provider=OpenAIProvider(api_key="secret", base_url="https://example.test/v1", http_client=client))
            with patch("deep_agent.agent.model_api_diagnostics.log_event", side_effect=lambda event, **kw: rows.append((event, kw))):
                with self.assertRaises(ModelHTTPError) as raised:
                    await Agent(model, deps_type=AgentDeps, capabilities=[RuntimeDiagnostics()]).run(
                        "private prompt", deps=AgentDeps(session_id="session", run_id="run"))
        self.assertEqual(raised.exception.status_code, 400)
        self.assertEqual([event for event, _ in rows], ["model_api_request", "model_api_response"])
        self.assertEqual(rows[0][1]["session_id"], "session")
        self.assertEqual(rows[1][1]["http_status"], 400)
        self.assertNotIn("private prompt", repr(rows))
        self.assertIsNone(_scope.get())

    async def test_concurrent_scopes_and_broken_logging_preserve_http(self):
        rows = []
        async def transport(request):
            await asyncio.sleep(0)
            return httpx2.Response(200, json={"ok": True})

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
            model = OpenAIChatModel("fixture", provider=OpenAIProvider(api_key="secret", http_client=client))
            async def send(run):
                with observe_model_api(model, {"run_id": run}, {}):
                    return await client.post("https://example.test/responses", json={"tools": [{"type": "function", "name": run}]})
            with patch("deep_agent.agent.model_api_diagnostics.log_event", side_effect=lambda event, **kw: rows.append((event, kw))):
                await asyncio.gather(send("one"), send("two"))
            with patch("deep_agent.agent.model_api_diagnostics.log_event", side_effect=RuntimeError("sink failed")):
                self.assertEqual((await send("three")).status_code, 200)
        for event, fields in rows:
            if event == "model_api_request":
                self.assertEqual(fields["tool_names"], [fields["run_id"]])
                self.assertEqual(fields["added_tool_names"], [fields["run_id"]])
        self.assertEqual(len(rows), 4)
        self.assertIsNone(_scope.get())

    async def test_lazy_sdk_stream_logs_requests_for_both_openai_protocols(self):
        for model_class in (OpenAIChatModel, OpenAIResponsesModel):
            rows = []
            async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda request: httpx2.Response(
                400, json={"error": {"message": "Tool names must be unique.", "type": "invalid_request_error"}}))) as client:
                model = model_class("fixture", provider=OpenAIProvider(api_key="secret", base_url="https://example.test/v1", http_client=client))
                with patch("deep_agent.agent.model_api_diagnostics.log_event", side_effect=lambda event, **kw: rows.append((event, kw))):
                    with self.assertRaises(ModelHTTPError) as raised:
                        agent = Agent(model, deps_type=AgentDeps, capabilities=[RuntimeDiagnostics()])
                        async with agent.run_stream_events("private image prompt", deps=AgentDeps(session_id="session", run_id="run")) as events:
                            async for _ in events:
                                pass
                self.assertEqual(raised.exception.status_code, 400)
                self.assertEqual([event for event, _ in rows], ["model_api_request", "model_api_response"])
                self.assertEqual(rows[0][1]["session_id"], "session")
                self.assertEqual(rows[1][1]["http_status"], 400)
                self.assertNotIn("private image prompt", repr(rows))
                self.assertIsNone(_scope.get())

    async def test_successful_lazy_stream_keeps_output_and_wire_body(self):
        wire, observations, results = [], [], []
        body = "".join("data: " + json.dumps({"id": "fixture", "object": "chat.completion.chunk", "created": 1,
            "model": "fixture", "choices": [{"index": 0, "delta": delta, "finish_reason": reason}]}) + "\n\n"
            for delta, reason in (({"role": "assistant", "content": "ok"}, None), ({}, "stop"))) + "data: [DONE]\n\n"
        async def transport(request):
            wire.append(json.loads(request.content))
            return httpx2.Response(200, content=body.encode(), headers={"content-type": "text/event-stream"})
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(transport)) as client:
            model = OpenAIChatModel("fixture", provider=OpenAIProvider(api_key="secret", http_client=client))
            for enabled in (False, True):
                agent = Agent(model, deps_type=AgentDeps, capabilities=[RuntimeDiagnostics()] if enabled else [])
                with patch("deep_agent.agent.model_api_diagnostics.log_event", side_effect=lambda event, **kw: observations.append((event, kw))):
                    async with agent.run_stream("private prompt", deps=AgentDeps(session_id="session")) as result:
                        results.append(await result.get_output())
        self.assertEqual(results, ["ok", "ok"])
        self.assertEqual(wire[0], wire[1])
        self.assertEqual([event for event, _ in observations], ["model_api_request", "model_api_response"])
        self.assertIsNone(_scope.get())

    async def test_response_stream_not_consumed_and_context_reset_on_cancel(self):
        class Stream(httpx2.AsyncByteStream):
            consumed = False
            async def __aiter__(self):
                self.consumed = True
                yield b"private streamed response"
        stream = Stream()
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(lambda request: httpx2.Response(200, stream=stream))) as client:
            model = OpenAIChatModel("fixture", provider=OpenAIProvider(api_key="secret", http_client=client))
            with observe_model_api(model, {"run_id": "run"}, {}):
                async with client.stream("POST", "https://example.test/chat/completions", json={"tools": []}):
                    self.assertFalse(stream.consumed)
            with self.assertRaises(asyncio.CancelledError):
                with observe_model_api(model, {}, {}):
                    raise asyncio.CancelledError()
        self.assertIsNone(_scope.get())
