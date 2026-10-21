"""Actual private HTTP endpoint and SDK wire contract (synthetic data only)."""
from __future__ import annotations

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class StructuredOutputCompatibleProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.refuse = False
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                owner.requests.append(payload)
                if owner.refuse:
                    body = json.dumps({"error": {"message": "Synthetic provider rejects tools"}}).encode()
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                elif payload.get("stream"):
                    chunks = [
                        {"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [
                            {"index": 0, "id": "private-call", "type": "function", "function": {
                                "name": "private_result", "arguments": '{"ok":true,"zero":0}'}}]}, "finish_reason": None}]},
                        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                        {"choices": [], "usage": {"prompt_tokens": 13, "completion_tokens": 9, "total_tokens": 22}},
                    ]
                    body = b"".join(("data: " + json.dumps({"id": "private", "object": "chat.completion.chunk",
                        "created": 1700000000, "model": "private-compatible", **chunk}) + "\n\n").encode() for chunk in chunks) + b"data: [DONE]\n\n"
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                else:
                    body = json.dumps({"id": "private", "object": "chat.completion", "created": 1700000000,
                        "model": "private-compatible", "choices": [{"index": 0, "message": {"role": "assistant",
                        "content": None, "tool_calls": [{"id": "private-call", "type": "function", "function": {
                            "name": "private_result", "arguments": '{"ok":true,"zero":0}'}}]}, "finish_reason": "tool_calls"}],
                        "usage": {"prompt_tokens": 13, "completion_tokens": 9, "total_tokens": 22}}).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.http = httpx.AsyncClient(trust_env=False)
        self.stores, self.engine, _ = await make_sqlite_stores()
        self.agent_id = await seed_default_agent(self.stores)
        self.persister = EventPersister(self.stores.coordinator)
        await self.persister.start()
        self.title = patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock)
        self.title.start()

    async def asyncTearDown(self):
        try:
            self.title.stop()
            await self.persister.aclose()
            await self.engine.dispose()
            await self.http.aclose()
        finally:
            await asyncio.to_thread(self.server.shutdown)
            self.server.server_close()
            await asyncio.to_thread(self.thread.join, 5)
            self.assertFalse(self.thread.is_alive())

    def runner(self):
        model = OpenAIChatModel("private-compatible", provider=OpenAIProvider(api_key="synthetic-key",
            base_url=f"http://127.0.0.1:{self.server.server_port}/v1", http_client=self.http))
        runner = AgentRunner(Agent(model, deps_type=AgentDeps), self.stores.coordinator, UsageLimits(request_limit=5))
        runner._output_config_loader = AsyncMock(return_value={"name": "private_result", "description": "Synthetic result",
            "json_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}, "zero": {"type": "integer"}},
                            "required": ["ok", "zero"], "additionalProperties": False}})
        return runner

    async def exercise(self, streaming):
        sid = uuid4().hex
        runner = self.runner()
        deps = AgentDeps(session_id=sid, agent_id=self.agent_id, user="synthetic-owner")
        if streaming:
            frames = [item async for item in runner.run_stream(sid, "Synthetic request", deps, persister=self.persister)]
            return frames[-1], sid
        return await runner.run(sid, "Synthetic request", deps), sid

    async def test_private_endpoint_normal_and_stream_keep_native_json_without_strict_mode(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                result, sid = await self.exercise(streaming)
                self.assertEqual(result["status"], "completed")
                self.assertEqual(result["output"], {"ok": True, "zero": 0})
                request = self.requests[-1]
                self.assertNotIn("response_format", request)
                output = next(tool["function"] for tool in request["tools"] if tool["function"]["name"] == "private_result")
                self.assertIsNot(output.get("strict"), True)
                self.assertEqual(request["tool_choice"], "required")
                rows = await self.stores.coordinator.list_all_events(sid)
                accepted = [r for r in rows if r.event_type == "output/attempt" and r.payload["accepted"]]
                self.assertEqual(len(accepted), 1)
                self.assertEqual(accepted[0].payload["output"], result["output"])
        self.assertEqual(len(self.requests), 2)

    async def test_provider_rejection_is_not_retried_as_plain_text(self):
        self.refuse = True
        with self.assertRaises(Exception) as error:
            await self.exercise(False)
        self.assertIn("400", str(error.exception))
        result, sid = await self.exercise(True)
        self.assertEqual(result["type"], "error")
        self.assertEqual(len(self.requests), 2)
        rows = await self.stores.coordinator.list_all_events(sid)
        self.assertFalse([r for r in rows if r.event_type == "output/attempt" and r.payload["accepted"]])
        self.assertTrue(all("tools" in request and request["tool_choice"] == "required" for request in self.requests))
