"""Business bootstrap permissions, with real auth dependencies and isolated DB.

The supplied user header represents identity established by the trusted gateway.
No LocalIdentityAdapter, identity middleware or dependency override is used.
"""
from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.messages import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel
from sqlalchemy import update

from deep_agent.api.v1 import api_router
from deep_agent.api.v1.deps import build_runner_for_agent
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.db.models import LlmModel, LlmProvider
from deep_agent.models.provider_registry import ProviderRuntimeError
from deep_agent.schemas.llm_model import LlmModelCreate
from deep_agent.security import ConsoleAdminAuthorizer, ConsoleAdminUnavailableError
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class ModelReadAccessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        self.checker = AsyncMock(side_effect=lambda user, headers: user == "enterprise-user")
        init_context(AppContext.for_testing(
            stores=self.stores, db_engine=self.engine, db_session_factory=self.factory,
            console_admin_authorizer=ConsoleAdminAuthorizer(self.checker),
        ))
        self.app = FastAPI()
        self.app.include_router(api_router)
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test",
                                  headers={"user": "ordinary-user", "authorization": "Bearer fixture"})
        self.model = (await self.stores.llm_model.list_all())[0]
        self.agent_id = await seed_default_agent(self.stores)
        await self.stores.integration_settings.save("deeptable-assistant", {"agent_id": self.agent_id})

    async def asyncTearDown(self):
        await self.client.aclose()
        await get_context().event_persister.aclose()
        reset_context()
        await self.engine.dispose()

    async def test_ordinary_client_bootstrap_gets_agent_default_and_model_labels(self):
        integration = await self.client.get("/api/v1/integrations/deeptable-assistant")
        self.assertEqual(integration.status_code, 200, integration.text)
        agent_id = integration.json()["settings"]["agent_id"]
        agent = await self.client.get(f"/api/v1/agents/{agent_id}")
        self.assertEqual(agent.status_code, 200, agent.text)
        self.assertTrue(agent.json()["name"])
        models = await self.client.get("/api/v1/llm-models")
        self.assertEqual(models.status_code, 200, models.text)
        selected = next(row for row in models.json() if row["id"] == agent.json()["model_id"])
        self.assertEqual(selected["name"], agent.json()["model"]["name"])
        for field in ("input_modalities", "tags", "context_window_tokens", "max_output_tokens"):
            self.assertIn(field, selected)
        detail = await self.client.get(f"/api/v1/llm-models/{selected['id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json(), selected)
        capabilities = await self.client.get("/api/v1/uploads/capabilities")
        self.assertEqual(capabilities.status_code, 200)
        self.checker.assert_not_called()

    async def test_missing_trusted_identity_is_rejected(self):
        for path in ("/api/v1/llm-models", f"/api/v1/llm-models/{self.model.id}",
                     f"/api/v1/agents/{self.agent_id}", "/api/v1/integrations/deeptable-assistant"):
            with self.subTest(path=path):
                response = await self.client.get(path, headers={"user": "  "})
                self.assertEqual(response.status_code, 401, response.text)
        self.checker.assert_not_called()

    async def test_business_reads_do_not_depend_on_admin_service_availability(self):
        self.checker.side_effect = ConsoleAdminUnavailableError("offline")
        for path in ("/api/v1/llm-models", f"/api/v1/llm-models/{self.model.id}"):
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
        self.checker.assert_not_called()
        full = await self.client.get("/api/v1/llm-models?selectable_only=false")
        self.assertEqual(full.status_code, 503, full.text)

    async def test_ordinary_user_cannot_access_management_or_full_inventory(self):
        mid = self.model.id
        operations = [
            ("GET", "/llm-models?selectable_only=false"),
            ("POST", "/llm-models"), ("PATCH", f"/llm-models/{mid}"),
            ("DELETE", f"/llm-models/{mid}"), ("POST", f"/llm-models/{mid}/test"),
            ("GET", "/llm-models/catalog"), ("GET", "/llm-models/catalog/export"),
            ("POST", "/llm-models/catalog/import"), ("POST", "/llm-models/catalog/refresh"),
            ("GET", "/agents"), ("GET", "/llm-providers"), ("GET", "/llm-provider-types"),
            ("GET", "/integrations"), ("GET", "/skills"), ("GET", "/mcp"),
            ("GET", "/ui-tools"), ("GET", "/global-settings"),
        ]
        for method, path in operations:
            with self.subTest(method=method, path=path):
                response = await self.client.request(method, "/api/v1" + path)
                self.assertEqual(response.status_code, 403, response.text)
        self.assertIsNotNone(await self.stores.llm_model.get_by_id(mid))

    async def test_admin_can_manage_models_and_read_full_fields(self):
        headers = {"user": "enterprise-user"}
        full = await self.client.get("/api/v1/llm-models?selectable_only=false", headers=headers)
        self.assertEqual(full.status_code, 200, full.text)
        self.assertIn("base_url", full.json()[0])
        self.assertIn("agent_count", full.json()[0])
        body = dict(provider_id=self.model.provider_id, name="admin-created", model="fixture-model",
                    input_modalities=["text"], context_window_tokens=32000, max_output_tokens=4096)
        created = await self.client.post("/api/v1/llm-models", json=body, headers=headers)
        self.assertEqual(created.status_code, 201, created.text)
        path = "/api/v1/llm-models/" + created.json()["id"]
        changed = await self.client.patch(path, json={"name": "admin-updated"}, headers=headers)
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertEqual(changed.json()["name"], "admin-updated")
        catalog = await self.client.get("/api/v1/llm-models/catalog", headers=headers)
        self.assertEqual(catalog.status_code, 200, catalog.text)
        deleted = await self.client.delete(path, headers=headers)
        self.assertEqual(deleted.status_code, 200, deleted.text)

    async def test_unavailable_models_filtered_and_cannot_be_used_by_runner(self):
        async with self.factory.begin() as db:
            await db.execute(update(LlmModel).where(LlmModel.id == self.model.id).values(enabled=False))
        models = await self.client.get("/api/v1/llm-models")
        self.assertEqual(models.status_code, 200, models.text)
        self.assertNotIn(self.model.id, [row["id"] for row in models.json()])
        detail = await self.client.get(f"/api/v1/llm-models/{self.model.id}")
        self.assertFalse(detail.json()["selectable"])
        self.assertEqual(detail.json()["unavailable_reason"], "model_disabled")
        with self.assertRaises(ProviderRuntimeError):
            await build_runner_for_agent(get_context(), self.agent_id, model_id=self.model.id)
        async with self.factory.begin() as db:
            await db.execute(update(LlmModel).where(LlmModel.id == self.model.id).values(enabled=True))
            await db.execute(update(LlmProvider).where(LlmProvider.id == self.model.provider_id).values(api_key=None))
        models = await self.client.get("/api/v1/llm-models")
        self.assertEqual(models.json(), [])

    async def test_business_responses_never_expose_provider_credentials_or_endpoint(self):
        secrets = ("fixture-raw-key", "fixture-access-token", "fixture-refresh-token", "private-gateway.invalid")
        async with self.factory.begin() as db:
            await db.execute(update(LlmProvider).where(LlmProvider.id == self.model.provider_id).values(
                api_key=secrets[0], base_url="https://private-gateway.invalid/v1",
                credential_json={"access_token": secrets[1], "refresh_token": secrets[2]},
            ))
        for path in ("/api/v1/llm-models", f"/api/v1/llm-models/{self.model.id}"):
            response = await self.client.get(path)
            self.assertEqual(response.status_code, 200, response.text)
            for secret in secrets:
                self.assertNotIn(secret, response.text)
            rows = response.json() if isinstance(response.json(), list) else [response.json()]
            for row in rows:
                for field in ("base_url", "agent_count", "created_at", "updated_at", "credential_json", "api_key"):
                    self.assertNotIn(field, row)
                self.assertNotIn("credential_json", row["provider"])
                self.assertTrue(row["api_key_set"])

    async def test_selected_model_id_reaches_runner_through_chat(self):
        selected = await self.stores.llm_configuration.create_model(LlmModelCreate(
            provider_id=self.model.provider_id, name="selected-model", model="selected-model",
            input_modalities=["text"], context_window_tokens=32000, max_output_tokens=4096,
        ))
        async def answer(messages, info):
            yield "selected model answer"

        def title(messages, info):
            return ModelResponse(parts=[TextPart("Model selection test")])

        # Only replace the external inference boundary. Keep HTTP identity,
        # model selection, real runner, SSE and persisted history in the test.
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(function=title, stream_function=answer)) as resolve:
            response = await self.client.post("/api/v1/chat/stream", json={
                "agent_id": self.agent_id, "model_id": selected.id,
                "message": "Hello", "session_id": "model-access-fixture",
            })
            self.assertEqual(response.status_code, 200, response.text)
            self.assertIn("selected model answer", response.text)
            self.assertNotIn("event: error", response.text)
            self.assertEqual(resolve.call_args.args[0].llm_model_id, selected.id)
        history = await self.client.get("/api/v1/sessions/model-access-fixture/history",
                                        params={"agent_id": self.agent_id})
        self.assertEqual(history.status_code, 200, history.text)
        self.assertIn("selected model answer", history.text)
        self.checker.assert_not_called()
