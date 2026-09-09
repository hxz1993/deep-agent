from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import patch
from uuid import uuid4
from types import SimpleNamespace

import httpx
from fastapi import FastAPI, HTTPException
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.providers.google import GoogleProvider
from sqlalchemy import text

from deep_agent.api.v1.admin import require_console_admin
from deep_agent.api.v1.llm_models import router
from deep_agent.db.deps import get_llm_configuration_service, get_llm_model_store
from deep_agent.models.connectivity import probe_model
from deep_agent.schemas.llm_model import LlmModelUpdate
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores


class ModelConnectivityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, _ = await make_sqlite_stores()
        self.row = (await self.stores.llm_model.list_all())[0]
        self.provider = await self.stores.llm_configuration.get_provider(self.row.provider_id)
        self.app = FastAPI()
        self.app.include_router(router, prefix="/api/v1")
        allow_console_admin(self.app)
        self.app.dependency_overrides[get_llm_model_store] = lambda: self.stores.llm_model
        self.app.dependency_overrides[get_llm_configuration_service] = lambda: self.stores.llm_configuration
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")
        self.url = f"/api/v1/llm-models/{self.row.id}/test?provider_id={self.provider.id}"

    async def asyncTearDown(self):
        await self.client.aclose()
        await self.engine.dispose()

    async def snapshot(self):
        async with self.engine.connect() as conn:
            return {table: [tuple(row) for row in (await conn.execute(text(f"SELECT * FROM {table}"))).all()]
                    for table in ("llm_models", "llm_providers", "chat_sessions", "chat_session_events")}

    def model(self, client):
        return OpenAIChatModel("test", provider=OpenAIProvider(api_key="fixture-secret", base_url="https://fixture.invalid/v1", http_client=client))

    async def test_real_sdk_request_is_single_bounded_and_does_not_write(self):
        requests = []
        def handle(request):
            payload = json.loads(request.content)
            requests.append(payload)
            return httpx.Response(200, json={"id": "probe", "object": "chat.completion", "created": 1700000000, "model": "test", "choices": [{"index": 0, "message": {"role": "assistant", "content": "OK"}, "finish_reason": "stop"}]})
        before = await self.snapshot()
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            with patch("deep_agent.models.connectivity.resolve_provider_model", return_value=self.model(client)) as resolve:
                response = await self.client.post(self.url)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["max_completion_tokens"], 64)
        self.assertNotIn("tools", requests[0])
        self.assertEqual(len(requests[0]["messages"]), 1)
        self.assertEqual(resolve.call_args.kwargs["model_name"], self.row.model)
        self.assertEqual(before, await self.snapshot())
        self.assertNotIn("fixture-secret", response.text)

    async def test_failed_http_is_safe_and_not_retried(self):
        for status, code in [(401, "authentication"), (403, "permission"), (404, "not_found"), (429, "rate_limit"), (503, "provider_error")]:
            calls = []
            def handle(request):
                calls.append(request)
                return httpx.Response(status, json={"error": {"message": "fixture-secret private-body https://token:secret@host/"}})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                with patch("deep_agent.models.connectivity.resolve_provider_model", return_value=self.model(client)):
                    response = await self.client.post(self.url)
            self.assertFalse(response.json()["ok"])
            self.assertEqual(response.json()["error_code"], code)
            self.assertEqual(len(calls), 1)
            self.assertNotIn("fixture-secret", response.text)
            self.assertNotIn("private-body", response.text)

    async def test_disabled_missing_and_moved_models_do_not_request(self):
        with patch("deep_agent.api.v1.llm_models.probe_model") as probe:
            self.assertEqual((await self.client.post(self.url.replace(self.row.id, str(uuid4())))).status_code, 404)
            self.assertEqual((await self.client.post(self.url.replace(self.provider.id, str(uuid4())))).status_code, 409)
            await self.stores.llm_configuration.update_model(self.row.id, LlmModelUpdate(enabled=False))
            self.assertEqual((await self.client.post(self.url)).status_code, 409)
            probe.assert_not_called()

    async def test_non_admin_and_unauthenticated_are_denied(self):
        for status in (401, 403):
            def denied():
                raise HTTPException(status_code=status, detail="denied")
            self.app.dependency_overrides[require_console_admin] = denied
            with patch("deep_agent.api.v1.llm_models.probe_model") as probe:
                self.assertEqual((await self.client.post(self.url)).status_code, status)
                probe.assert_not_called()

    async def test_busy_and_missing_credentials_do_not_request(self):
        from deep_agent.schemas.llm_provider import LlmProviderUpdate
        with patch("deep_agent.api.v1.llm_models.probe_model") as probe:
            with patch("deep_agent.api.v1.llm_models._model_test_slots", asyncio.Semaphore(0)):
                self.assertEqual((await self.client.post(self.url)).status_code, 429)
            await self.stores.llm_configuration.update_provider(self.provider.id, LlmProviderUpdate(clear_api_key=True))
            response = await self.client.post(self.url)
            self.assertEqual(response.status_code, 409)
            self.assertIn("API Key", response.json()["detail"])
            probe.assert_not_called()

    async def test_google_probe_disables_sdk_retries(self):
        calls = []
        def handle(request):
            calls.append(request)
            return httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "private-fixture-error"}})
        provider = SimpleNamespace(provider_type="google", api_key="fixture-secret", use_default_base_url=True, base_url=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            def google_provider(**kwargs):
                kwargs["http_client"] = client
                return GoogleProvider(**kwargs)
            with patch("deep_agent.models.provider_registry.GoogleProvider", side_effect=google_provider):
                result = await probe_model(self.row, provider)
        self.assertEqual(result.error_code, "rate_limit")
        self.assertEqual(len(calls), 1)
        self.assertNotIn("private-fixture-error", result.message)

    async def test_dedicated_openai_compatible_providers_do_not_retry(self):
        from deep_agent.models import provider_registry

        for provider_type, class_name in [
            ("deepseek", "DeepSeekProvider"), ("moonshotai", "MoonshotAIProvider"),
            ("alibaba", "AlibabaProvider"), ("zai", "ZaiProvider"),
            ("openrouter", "OpenRouterProvider"),
        ]:
            for status in (429, 503):
                with self.subTest(provider=provider_type, status=status):
                    calls = []
                    provider_class = getattr(provider_registry, class_name)
                    def handle(request):
                        calls.append(request)
                        return httpx.Response(status, json={"error": {"message": "private-fixture-error"}})
                    provider = SimpleNamespace(provider_type=provider_type, api_key="fixture-secret", use_default_base_url=True, base_url=None)
                    row = SimpleNamespace(model="openai/test-model", input_modalities=["text"], tags=[], max_output_tokens=64)
                    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                        def build_provider(**kwargs):
                            return provider_class(**kwargs, http_client=client)
                        with patch.object(provider_registry, class_name, side_effect=build_provider):
                            result = await probe_model(row, provider)
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(result.error_code, "rate_limit" if status == 429 else "provider_error")
                    self.assertNotIn("private-fixture-error", result.message)

    async def test_timeout_and_cancellation_close_model_context(self):
        entered = asyncio.Event()
        class WaitingModel:
            provider = None
            closed = False
            async def __aenter__(self): return self
            async def __aexit__(self, *args): self.closed = True
            async def request(self, *args):
                entered.set()
                await asyncio.Event().wait()
        model = WaitingModel()
        with patch("deep_agent.models.connectivity.resolve_provider_model", return_value=model), patch("deep_agent.models.connectivity.TEST_TIMEOUT_SECONDS", .01):
            result = await probe_model(self.row, self.provider)
        self.assertEqual(result.error_code, "timeout")
        self.assertTrue(model.closed)
        model.closed = False
        entered.clear()
        with patch("deep_agent.models.connectivity.resolve_provider_model", return_value=model):
            task = asyncio.create_task(probe_model(self.row, self.provider))
            await entered.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
        self.assertTrue(model.closed)
