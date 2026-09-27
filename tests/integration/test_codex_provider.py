from __future__ import annotations

import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials

from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.db.models import LlmProvider
from deep_agent.services.codex_provider import DatabaseCodexCredentialSource, FLOWS, CodexUnavailable
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores


class CodexProviderTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        # Keep the actual OS ownership lock, but isolate it from the running
        # developer service and from other tests in this process.
        from deep_agent.services import codex_provider
        temporary = tempfile.TemporaryDirectory(prefix="TMP_to_delete_codex_owner_")
        self.addCleanup(temporary.cleanup)
        path_patch = patch("deep_agent.services.codex_provider.Path", wraps=Path)
        path_proxy = path_patch.start()
        path_proxy.home.return_value = Path(temporary.name)
        self.addCleanup(path_patch.stop)
        owner_patch = patch("deep_agent.services.codex_provider._owner_file", None)
        owner_patch.start()
        self.addCleanup(owner_patch.stop)
        def release_test_owner():
            if codex_provider._owner_file is not None:
                codex_provider._owner_file.close()
                codex_provider._owner_file = None
        self.addCleanup(release_test_owner)
        self._env = patch.dict(os.environ, {"DEEP_AGENT_ENABLE_SUBSCRIPTION_PROVIDER": "true"})
        self._env.start()
        FLOWS._flows.clear()
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        app = FastAPI()
        app.include_router(api_router)
        allow_console_admin(app)
        init_context(AppContext.for_testing(stores=self.stores, db_engine=self.engine, db_session_factory=self.factory))
        self.client = AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test", headers={"user": "test-admin"})

    async def asyncTearDown(self) -> None:
        FLOWS._flows.clear()
        await self.client.aclose()
        await get_context().event_persister.aclose()
        reset_context()
        await self.engine.dispose()
        self._env.stop()

    async def _verified_flow(self, provider_id: str | None = None) -> str:
        started = await self.client.post("/api/v1/llm-providers/codex/authorization", json={"provider_id": provider_id})
        self.assertEqual(started.status_code, 200, started.text)
        flow_id = started.json()["flow_id"]
        state = parse_qs(urlsplit(started.json()["authorization_url"]).query)["state"][0]
        callback = f"http://localhost:1455/auth/callback?code=test-code&state={state}"
        credentials = OpenAICodexCredentials(access_token="access-secret", refresh_token="refresh-secret", account_id="account-1234")
        with patch("pydantic_ai.providers.openai_codex.OpenAICodexOAuthFlow.exchange_code", return_value=credentials):
            verified = await self.client.post(f"/api/v1/llm-providers/codex/authorization/{flow_id}/verify", json={"provider_id": provider_id, "callback_url": callback})
        self.assertEqual(verified.status_code, 200, verified.text)
        self.assertNotIn("secret", verified.text)
        return flow_id

    async def test_authorize_save_refresh_disconnect_and_gate(self) -> None:
        flow_id = await self._verified_flow()
        created = await self.client.post("/api/v1/llm-providers", json={
            "name": "GPT 本机", "provider_type": "openai_codex", "use_default_base_url": True,
            "authorization_flow_id": flow_id,
        })
        self.assertEqual(created.status_code, 201, created.text)
        provider_id = created.json()["id"]
        self.assertTrue(created.json()["authenticated"])
        self.assertEqual(created.json()["account_hint"], "••••1234")
        self.assertNotIn("secret", created.text)
        self.assertFalse(created.json()["api_key_set"])
        replay = await self.client.post("/api/v1/llm-providers", json={
            "name": "replay", "provider_type": "openai_codex", "use_default_base_url": True,
            "authorization_flow_id": flow_id,
        })
        self.assertEqual(replay.status_code, 409)
        model = await self.client.post("/api/v1/llm-models", json={
            "provider_id": provider_id, "name": "gpt-6-luna", "model": "gpt-6-luna",
            "input_modalities": ["text"], "context_window_tokens": 100000,
            "max_output_tokens": 8000,
        })
        self.assertEqual(model.status_code, 201, model.text)
        self.assertEqual(model.json()["purpose"], "chat")
        self.assertTrue(model.json()["selectable"])

        source = DatabaseCodexCredentialSource(provider_id, 1)
        self.assertEqual((await source.load()).account_id, "account-1234")
        renamed = await self.client.patch(f"/api/v1/llm-providers/{provider_id}", json={"name": "GPT renamed"})
        self.assertEqual(renamed.status_code, 200, renamed.text)
        await source.save(OpenAICodexCredentials(access_token="new-access", refresh_token="new-refresh", account_id="account-1234"))
        async with self.factory() as db:
            row = await db.get(LlmProvider, provider_id)
            self.assertEqual(row.name, "GPT renamed")
            self.assertEqual(row.auth_revision, 2)
            self.assertEqual(row.credential_json["refresh_token"], "new-refresh")
        disconnected = await self.client.post(f"/api/v1/llm-providers/{provider_id}/disconnect")
        self.assertEqual(disconnected.status_code, 200, disconnected.text)
        self.assertFalse(disconnected.json()["authenticated"])
        with self.assertRaises(CodexUnavailable):
            await source.save(OpenAICodexCredentials(access_token="old", refresh_token="old", account_id="account-1234"))
        unavailable = await self.client.get(f"/api/v1/llm-models/{model.json()['id']}")
        self.assertEqual(unavailable.json()["unavailable_reason"], "provider_unconfigured")
        with patch.dict(os.environ, {"DEEP_AGENT_ENABLE_SUBSCRIPTION_PROVIDER": "false"}):
            self.assertEqual((await self.client.get(f"/api/v1/llm-models/{model.json()['id']}")).json()["unavailable_reason"], "provider_disabled")
            self.assertEqual((await self.client.post("/api/v1/llm-providers/codex/authorization", json={})).status_code, 409)
            self.assertEqual((await self.client.get(f"/api/v1/llm-providers/{provider_id}")).status_code, 200)

    async def test_callback_state_cancel_and_owner_are_enforced(self) -> None:
        started = await self.client.post("/api/v1/llm-providers/codex/authorization", json={})
        flow_id = started.json()["flow_id"]
        wrong_state = await self.client.post(f"/api/v1/llm-providers/codex/authorization/{flow_id}/verify", json={"callback_url": "http://localhost:1455/auth/callback?code=x&state=wrong"})
        self.assertEqual(wrong_state.status_code, 409)
        malformed = await self.client.post(f"/api/v1/llm-providers/codex/authorization/{flow_id}/verify", json={"callback_url": "http://[invalid/auth/callback?code=x"})
        self.assertEqual(malformed.status_code, 409)
        wrong_owner = await self.client.post(f"/api/v1/llm-providers/codex/authorization/{flow_id}/cancel", json={}, headers={"user": "another-admin"})
        self.assertEqual(wrong_owner.status_code, 409)
        cancelled = await self.client.post(f"/api/v1/llm-providers/codex/authorization/{flow_id}/cancel", json={})
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual((await self.client.post(f"/api/v1/llm-providers/codex/authorization/{flow_id}/verify", json={"callback_url": "http://localhost:1455/auth/callback?code=x&state=wrong"})).status_code, 409)

        expired = await self._verified_flow()
        FLOWS._flows[expired].deadline = 0
        self.assertEqual((await self.client.post("/api/v1/llm-providers", json={
            "name": "expired", "provider_type": "openai_codex", "use_default_base_url": True,
            "authorization_flow_id": expired,
        })).status_code, 409)

    async def test_failed_save_keeps_verified_flow_and_reauthorization_replaces_credentials(self) -> None:
        original = await self._verified_flow()
        payload = {"name": "GPT 本机", "provider_type": "openai_codex", "use_default_base_url": True}
        created = await self.client.post("/api/v1/llm-providers", json={**payload, "authorization_flow_id": original})
        self.assertEqual(created.status_code, 201, created.text)
        provider_id = created.json()["id"]

        pending = await self._verified_flow()
        duplicate = await self.client.post("/api/v1/llm-providers", json={**payload, "authorization_flow_id": pending})
        self.assertEqual(duplicate.status_code, 409)
        self.assertIn(pending, FLOWS._flows)
        retry = await self.client.post("/api/v1/llm-providers", json={**payload, "name": "GPT 备用", "authorization_flow_id": pending})
        self.assertEqual(retry.status_code, 201, retry.text)
        self.assertNotIn(pending, FLOWS._flows)

        old_source = DatabaseCodexCredentialSource(provider_id, 1)
        renewed = await self._verified_flow(provider_id)
        self.assertEqual((await self.client.patch(f"/api/v1/llm-providers/{provider_id}", json={
            "name": "GPT 已重连", "authorization_flow_id": renewed,
        })).status_code, 200)
        with self.assertRaises(CodexUnavailable):
            await old_source.save(OpenAICodexCredentials(access_token="stale", refresh_token="stale", account_id="account-1234"))
        with self.assertRaises(CodexUnavailable):
            await old_source.load()
        self.assertEqual((await self.client.patch(f"/api/v1/llm-providers/{provider_id}", json={
            "credential_json": {"access_token": "forbidden"},
        })).status_code, 422)
        async with self.factory() as db:
            row = await db.get(LlmProvider, provider_id)
            self.assertEqual(row.name, "GPT 已重连")
            self.assertEqual(row.auth_revision, 2)
            self.assertNotEqual(row.credential_json["access_token"], "stale")
