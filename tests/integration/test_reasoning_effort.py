from __future__ import annotations

import asyncio
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from openai import AsyncOpenAI
from pydantic_ai.exceptions import ModelHTTPError
from pydantic_ai.messages import ModelRequest, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai_codex import OpenAICodexModel
from pydantic_ai.providers.openai_codex import OpenAICodexProvider

from deep_agent.agent.runner import AgentRunner
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.db.models import Agent as AgentRow, LlmModel
from deep_agent.models.provider_registry import OPENAI_RESPONSES_TAG, ProviderRuntimeConfig, resolve_provider_model
from deep_agent.models.reasoning import available_efforts, effective_agent_effort, model_binding, reasoning_settings
from deep_agent.schemas.agent import AgentCreate, AgentUpdate
from deep_agent.schemas.llm_model import LlmModelCreate, LlmModelUpdate
from deep_agent.schemas.llm_provider import LlmProviderCreate
from deep_agent.services.llm_configuration import LlmConfigurationValidationError
from tests.helpers.request_identity import TEST_USER_ID, install_test_user_header
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.integration.helpers.chat import mock_request_disconnect, parse_sse_events
from tests.unit.test_model_catalog_store import catalog_row


class ReasoningEffortIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.wire = []
        requests = self.wire

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                requests.append(request)
                if request.get("model") not in {"synthetic-effort-a", "synthetic-effort-b", "replacement"}:
                    encoded = b'{"error":{"message":"synthetic capture","type":"invalid_request_error"}}'
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(encoded)))
                    self.end_headers()
                    self.wfile.write(encoded)
                    return
                root = {"id": "synthetic", "created": 1700000000, "model": request["model"]}
                if self.path.endswith("/responses"):
                    body = json.dumps({
                        **root, "id": "resp-synthetic", "object": "response", "created_at": root["created"],
                        "status": "completed", "output": [{
                            "type": "message", "id": "msg-synthetic", "status": "completed", "role": "assistant",
                            "content": [{"type": "output_text", "text": "ok", "annotations": []}],
                        }],
                    }).encode()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                usage = {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13}
                answered_tools = sum(message["role"] == "tool" for message in request["messages"])
                if request.get("stream") and request.get("tools") and answered_tools < 2 and any(
                    "pause for confirmation" in str(message.get("content", "")) for message in request["messages"]
                ):
                    chunks = [
                        {"choices": [{"index": 0, "delta": {"role": "assistant", "tool_calls": [{
                            "index": 0, "id": f"synthetic-confirm-{answered_tools}", "type": "function",
                            "function": {"name": "builtin__confirm", "arguments": '{"message":"Continue?"}'},
                        }]}, "finish_reason": None}]},
                        {"choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}]},
                        {"choices": [], "usage": usage},
                    ]
                elif request.get("stream"):
                    chunks = [
                        {"choices": [{"index": 0, "delta": {"role": "assistant", "content": "ok"}, "finish_reason": None}]},
                        {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]},
                        {"choices": [], "usage": usage},
                    ]
                if request.get("stream"):
                    body = "".join("data: " + json.dumps({
                        **root, "object": "chat.completion.chunk", **chunk,
                    }) + "\n\n" for chunk in chunks) + "data: [DONE]\n\n"
                    content_type = "text/event-stream"
                else:
                    body = json.dumps({**root, "object": "chat.completion", "usage": usage, "choices": [
                        {"index": 0, "message": {"role": "assistant", "content": "ok"}, "finish_reason": "stop"},
                    ]})
                    content_type = "application/json"
                encoded = body.encode()
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addAsyncCleanup(self.close_server)
        self.stores, engine, factory = await make_sqlite_stores()
        self.addAsyncCleanup(engine.dispose)
        init_context(AppContext.for_testing(
            registry=build_registry(), stores=self.stores, db_engine=engine, db_session_factory=factory,
        ))
        self.addCleanup(reset_context)
        self.addAsyncCleanup(get_context().event_persister.aclose)
        app = FastAPI()
        install_test_user_header(app)
        app.include_router(api_router)
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url="http://test")
        self.addAsyncCleanup(self.client.aclose)
        title = patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock)
        title.start()
        self.addCleanup(title.stop)
        self.provider = await self.stores.llm_configuration.create_provider(LlmProviderCreate(
            name="synthetic-effort", provider_type="openai_compatible", use_default_base_url=False,
            base_url=f"http://127.0.0.1:{self.server.server_port}/v1", api_key="synthetic",
        ))
        self.model = await self.make_model("synthetic-effort-a")
        self.other = await self.make_model("synthetic-effort-b")
        self.agent = await self.stores.agent.create_unique(AgentCreate(
            name="synthetic-reasoning-agent", prompt="Answer briefly.", model_id=self.model.id, reasoning_effort="high",
        ))

    async def close_server(self):
        await asyncio.to_thread(self.server.shutdown)
        self.server.server_close()
        await asyncio.to_thread(self.thread.join, 5)

    async def make_model(self, code):
        return await self.stores.llm_configuration.create_model(LlmModelCreate(
            provider_id=self.provider.id, name=code, model=code, input_modalities=["text"],
            context_window_tokens=32_768, max_output_tokens=1024,
            reasoning_options=[{"type": "effort", "values": ["low", "high"]}],
        ))

    async def test_real_http_inheritance_stream_override_and_old_history(self):
        old = ModelRequest(parts=[UserPromptPart(content="before 014")])
        await self.stores.coordinator.append(
            "effort-history", [old], agent_id=self.agent.id, user_id=TEST_USER_ID,
            turn_id="legacy-turn", run_id="legacy-run",
        )
        source = {"agent_id": self.agent.id, "session_id": "effort-history", "message": "new request"}
        for streaming, extra, expected in [
            (False, {}, "high"),
            (True, {"reasoning_effort": "low"}, "low"),
            (False, {"reasoning_effort": "default"}, None),
            (True, {"model_id": self.other.id}, None),
        ]:
            with self.subTest(streaming=streaming, extra=extra), mock_request_disconnect():
                response = await self.client.post("/api/v1/chat/stream" if streaming else "/api/v1/chat", json={**source, **extra})
                self.assertEqual(response.status_code, 200, response.text)
                if streaming:
                    self.assertEqual(parse_sse_events(response.text)[-1]["type"], "final")
                self.assertEqual(self.wire[-1].get("reasoning_effort"), expected)
                if expected is None:
                    self.assertNotIn("reasoning_effort", self.wire[-1])
        self.assertEqual(len(self.wire), 4)
        response = await self.client.post("/api/v1/chat", json={**source, "reasoning_effort": "xhigh"})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(len(self.wire), 4)
        rows = await self.stores.coordinator.list_all_events("effort-history")
        recorded = [(row.event_type, (row.payload.get("message", {}).get("metadata") or {}).get("reasoning_effort")) for row in rows]
        self.assertEqual([value for _, value in recorded if value], ["high", "low", "default", "default"])
        self.assertTrue(all(kind == "user/message" for kind, value in recorded if value))
        self.assertNotIn("reasoning_effort", rows[0].payload["message"].get("metadata") or {})
        history = await self.client.get("/api/v1/sessions/effort-history/history")
        self.assertEqual(history.status_code, 200, history.text)
        users = [item for item in history.json()["items"] if item["kind"] == "user_message"]
        self.assertEqual([item.get("reasoning_effort") for item in users], [None, "high", "low", "default", "default"])
        self.assertNotIn("reasoning_effort", users[0])

    async def test_pending_ui_tool_reuses_original_model_and_effort(self):
        await self.stores.agent.update_unique(self.agent.id, AgentUpdate(capabilities=["ui:builtin"]))
        request = {"agent_id": self.agent.id, "session_id": "effort-pending"}
        with mock_request_disconnect():
            response = await self.client.post("/api/v1/chat/stream", json={
                **request, "message": "pause for confirmation", "reasoning_effort": "low",
            })
        self.assertEqual(response.status_code, 200, response.text)
        pending = parse_sse_events(response.text)[-1]
        self.assertEqual(pending["status"], "pending", response.text)
        call_id = pending["deferred"]["calls"][0]["tool_call_id"]
        await self.stores.agent.update_unique(self.agent.id, AgentUpdate(model_id=self.other.id, reasoning_effort="high"))
        resume = {**request, "deferred_tool_results": [{"tool_call_id": call_id, "result": {"confirmed": True}}]}
        response = await self.client.post("/api/v1/chat/stream", json={**resume, "reasoning_effort": "high"})
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.wire), 1)
        await self.stores.llm_configuration.update_model(self.model.id, LlmModelUpdate(enabled=False))
        response = await self.client.post("/api/v1/chat/stream", json=resume)
        self.assertEqual(response.status_code, 409)
        self.assertEqual(len(self.wire), 1)
        await self.stores.llm_configuration.update_model(self.model.id, LlmModelUpdate(enabled=True))
        replacement_provider = await self.stores.llm_configuration.create_provider(LlmProviderCreate(
            name="replacement-provider", provider_type="openai_compatible", use_default_base_url=False,
            base_url=self.provider.base_url, api_key="synthetic",
        ))
        for provider in (replacement_provider, self.provider):
            await self.stores.llm_configuration.update_model(self.model.id, LlmModelUpdate(
                provider_id=provider.id, reasoning_options=[{"type": "effort", "values": ["low", "high"]}],
            ))
            if provider.id == replacement_provider.id:
                response = await self.client.post("/api/v1/chat/stream", json=resume)
                self.assertEqual(response.status_code, 409)
                self.assertEqual(len(self.wire), 1)
        with mock_request_disconnect():
            response = await self.client.post("/api/v1/chat/stream", json=resume)
        self.assertEqual(response.status_code, 200, response.text)
        pending_again = parse_sse_events(response.text)[-1]
        self.assertEqual(pending_again["status"], "pending", response.text)
        resume["deferred_tool_results"][0]["tool_call_id"] = pending_again["deferred"]["calls"][0]["tool_call_id"]
        with mock_request_disconnect():
            response = await self.client.post("/api/v1/chat/stream", json=resume)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(parse_sse_events(response.text)[-1]["status"], "completed", response.text)
        self.assertEqual([(body["model"], body["reasoning_effort"]) for body in self.wire],
                         [(self.model.model, "low")] * 3)
        history = (await self.client.get("/api/v1/sessions/effort-pending/history")).json()
        self.assertEqual([item["reasoning_effort"] for item in history["items"] if item["kind"] == "user_message"], ["low"])

    async def test_native_provider_effort_request_bodies(self):
        cases = [
            ("openai", "gpt-5", [OPENAI_RESPONSES_TAG], "high", {"reasoning": {"effort": "high"}}),
            ("alibaba", "qwen3.8-flash", [], "low", {"reasoning_effort": "low", "enable_thinking": True}),
            ("deepseek", "deepseek-flash", [], "max", {"reasoning_effort": "max", "thinking": {"type": "enabled"}}),
            ("moonshotai", "kimi-k3", [], "low", {"reasoning_effort": "low"}),
            ("zai", "glm-5.2", [], "high", {"reasoning_effort": "high"}),
            ("stepfun", "step-3.7-flash", [], "high", {"reasoning_effort": "high"}),
            ("openrouter", "openai/gpt-5", [], "high", {"reasoning": {"effort": "high"}}),
            ("anthropic", "claude-opus-4-7", [], "high", {"output_config": {"effort": "high"}, "thinking": {"type": "adaptive"}}),
            ("google", "gemini-3.1-pro-preview", [], "high", {"generationConfig": {"thinkingConfig": {"thinking_level": "HIGH"}}}),
        ]

        def assert_fields(actual, expected):
            for key, value in expected.items():
                self.assertIn(key, actual)
                if isinstance(value, dict):
                    assert_fields(actual[key], value)
                else:
                    self.assertEqual(actual[key], value)

        for provider_type, name, tags, effort, expected in cases:
            with self.subTest(provider=provider_type):
                provider = ProviderRuntimeConfig(
                    id="synthetic", name=provider_type, provider_type=provider_type,
                    use_default_base_url=False, base_url=f"http://127.0.0.1:{self.server.server_port}/v1",
                    api_key="synthetic",
                )
                settings = reasoning_settings(SimpleNamespace(model=name, tags=tags), provider, effort)
                self.assertIsNotNone(settings)
                model = resolve_provider_model(model_name=name, provider=provider, tags=tags, http_retry_attempts=1)
                before = len(self.wire)
                with self.assertRaises(ModelHTTPError):
                    await model.request([ModelRequest(parts=[UserPromptPart(content="hello")])], settings, ModelRequestParameters())
                self.assertEqual(len(self.wire), before + 1)
                assert_fields(self.wire[-1], expected)

    async def test_codex_responses_preserves_existing_summary_with_effort(self):
        base_url = f"http://127.0.0.1:{self.server.server_port}/v1"
        runtime = ProviderRuntimeConfig(
            id="synthetic", name="Codex fixture", provider_type="openai_codex",
            use_default_base_url=True, base_url="", api_key="",
            auth_method="oauth", auth_status="connected",
        )
        async with AsyncOpenAI(api_key="synthetic", base_url=base_url) as client:
            transport = OpenAICodexProvider(openai_client=client)
            sdk = OpenAICodexModel("gpt-6.1-sol", provider=transport)
            with patch.dict("os.environ", {"DEEP_AGENT_ENABLE_SUBSCRIPTION_PROVIDER": "true"}), patch(
                "deep_agent.services.codex_provider.OpenAICodexProvider", return_value=transport,
            ), patch("deep_agent.services.codex_provider._claim_single_owner"):
                model = resolve_provider_model(model_name=sdk.model_name, provider=runtime)
                for effort in (None, "high"):
                    settings = reasoning_settings(SimpleNamespace(model=sdk.model_name, tags=[]), runtime, effort) if effort else None
                    with self.assertRaises(ModelHTTPError):
                        await model.request([ModelRequest(parts=[UserPromptPart(content="hello")])], settings, ModelRequestParameters())
                    reasoning = self.wire[-1]["reasoning"]
                    self.assertEqual(reasoning["summary"], "auto")
                    self.assertEqual(reasoning.get("effort"), effort)

    async def test_configuration_binding_changes_and_old_updates(self):
        stores = self.stores
        self.assertEqual((await stores.agent.update_unique(self.agent.id, AgentUpdate(prompt="changed"))).reasoning_effort["value"], "high")
        await stores.llm_configuration.update_model(self.model.id, LlmModelUpdate(name="renamed"))
        response = await self.client.post("/api/v1/chat", json={"agent_id": self.agent.id, "message": "keep"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(self.wire[-1]["reasoning_effort"], "high")
        await stores.llm_configuration.update_model(self.model.id, LlmModelUpdate(
            model="replacement", reasoning_options=[{"type": "effort", "values": ["high"]}],
        ))
        response = await self.client.post("/api/v1/chat", json={"agent_id": self.agent.id, "message": "fall back"})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("reasoning_effort", self.wire[-1])
        changed = await stores.agent.update_unique(self.agent.id, AgentUpdate(model_id=self.other.id))
        self.assertIsNone(changed.reasoning_effort)

    async def test_switching_openai_protocol_keeps_saved_model_and_agent_effort(self):
        responses = await self.stores.llm_configuration.update_model(
            self.model.id, LlmModelUpdate(tags=[OPENAI_RESPONSES_TAG]),
        )
        self.assertEqual(available_efforts(responses, self.provider), ["low", "high"])
        self.assertEqual(effective_agent_effort(self.agent, responses, self.provider), "high")

        agent = await self.stores.agent.update_unique(self.agent.id, AgentUpdate(reasoning_effort="low"))
        chat = await self.stores.llm_configuration.update_model(self.model.id, LlmModelUpdate(tags=[]))
        self.assertEqual(available_efforts(chat, self.provider), ["low", "high"])
        self.assertEqual(effective_agent_effort(agent, chat, self.provider), "low")

    async def test_catalog_effort_accepts_native_and_custom_routes_for_the_same_model(self):
        code = "synthetic-effort-a"
        entry = catalog_row(code)
        entry["capabilities"]["reasoning_options"] = [{"type": "effort", "values": ["low", "high"]}]
        await self.stores.model_catalog.replace_all([entry])
        reference = {"provider_id": "openai", "provider_model_id": code}
        with self.assertRaisesRegex(LlmConfigurationValidationError, "目录档位与模型接入不匹配"):
            await self.stores.llm_configuration.update_model(self.other.id, LlmModelUpdate(reasoning_catalog=reference))
        native = await self.stores.llm_configuration.create_provider(LlmProviderCreate(
            name="native-catalog-provider", provider_type="openai", use_default_base_url=False,
            base_url=self.provider.base_url, api_key="synthetic",
        ))
        for provider in (native, self.provider):
            with self.subTest(provider=provider.provider_type):
                model = await self.stores.llm_configuration.create_model(LlmModelCreate(
                    provider_id=provider.id, name="from-catalog", model=code, input_modalities=["text"],
                    context_window_tokens=32768, max_output_tokens=1024, reasoning_catalog=reference,
                ))
                agent = await self.stores.agent.update_unique(self.agent.id, AgentUpdate(model_id=model.id, reasoning_effort="low"))
                response = await self.client.post("/api/v1/chat", json={"agent_id": agent.id, "message": "native code"})
                self.assertEqual(response.status_code, 200, response.text)
                expected_effort = (self.wire[-1]["reasoning"]["effort"] if provider.provider_type == "openai"
                                   else self.wire[-1]["reasoning_effort"])
                self.assertEqual((self.wire[-1]["model"], expected_effort), (code, "low"))
                self.assertEqual(model.reasoning_options["source"], "catalog")

    async def test_openai_legacy_marker_and_chat_bindings_survive_save(self):
        provider = await self.stores.llm_configuration.create_provider(LlmProviderCreate(
            name="legacy OpenAI fixture", provider_type="openai", use_default_base_url=False,
            base_url=self.provider.base_url, api_key="synthetic",
        ))
        model = await self.stores.llm_configuration.create_model(LlmModelCreate(
            provider_id=provider.id, name="legacy", model=self.model.model, input_modalities=["text"],
            context_window_tokens=32768, max_output_tokens=1024,
            tags=["tool_call", OPENAI_RESPONSES_TAG],
            reasoning_options=[{"type": "effort", "values": ["high"]}],
        ))
        self.assertEqual(model.tags, ["tool_call"])
        agent = await self.stores.agent.update_unique(
            self.agent.id, AgentUpdate(model_id=model.id, reasoning_effort="high"),
        )
        legacy_binding = model_binding(model, provider, protocol="openai_chat")
        async with get_context().db_session_factory.begin() as db:
            saved = await db.get(LlmModel, model.id)
            saved.tags = [*model.tags, OPENAI_RESPONSES_TAG]
            saved.reasoning_options = {**model.reasoning_options, "binding": legacy_binding}
            saved_agent = await db.get(AgentRow, agent.id)
            saved_agent.reasoning_effort = {"value": "high", "binding": legacy_binding}
        legacy_agent = SimpleNamespace(
            model_id=agent.model_id, reasoning_effort={"value": "high", "binding": legacy_binding},
        )
        for expected_tags in (["tool_call", OPENAI_RESPONSES_TAG], ["tool_call"]):
            saved = await self.stores.llm_model.get_by_id(model.id)
            self.assertEqual(saved.tags, expected_tags)
            self.assertEqual(effective_agent_effort(legacy_agent, saved, provider), "high")
            response = await self.client.post("/api/v1/chat", json={"agent_id": agent.id, "message": "hello"})
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.wire[-1]["reasoning"]["effort"], "high")
            self.assertIn("input", self.wire[-1])
            await self.stores.llm_configuration.update_model(model.id, LlmModelUpdate(description="saved"))
