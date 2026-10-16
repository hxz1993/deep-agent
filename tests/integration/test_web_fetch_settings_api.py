"""SQLite/API regression for the Search + Fetch settings compatibility contract."""
from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.schemas.llm_provider import LlmProviderCreate
from deep_agent.web.settings import WEB_SEARCH_SETTING_KEY
from tests.helpers.sqlite_stores import make_sqlite_stores_sync


class WebFetchSettingsApiTests(unittest.TestCase):
    url = "/api/v1/global-settings/web-search"
    headers = {"user": "enterprise-admin"}

    def setUp(self) -> None:
        self.stores, self.engine, factory = make_sqlite_stores_sync()
        init_context(AppContext.for_testing(
            stores=self.stores, db_engine=self.engine, db_session_factory=factory,
        ))
        app = FastAPI()
        app.include_router(api_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        reset_context()
        asyncio.run(self.engine.dispose())

    def stored(self):
        return asyncio.run(self.stores.global_setting.get(WEB_SEARCH_SETTING_KEY))

    def seed(self, value):
        return asyncio.run(self.stores.global_setting.upsert(
            WEB_SEARCH_SETTING_KEY, value=value, version=1,
        ))

    def put(self, value):
        response = self.client.put(self.url, headers=self.headers, json={"version": 1, "value": value})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["value"]

    def test_unconfigured_and_historical_reads_do_not_rewrite_stored_values(self) -> None:
        response = self.client.get(self.url, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["value"]["fetch_mode"], "system")
        self.assertIsNone(self.stored())

        historical = {"mode": "provider", "provider": "deepseek",
                      "api_key": "fixture-legacy-key", "model": "legacy-search-model"}
        original = self.seed(historical)
        response = self.client.get(self.url, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        value = response.json()["value"]
        self.assertIsNone(value["model"])
        self.assertEqual(value["fetch_mode"], "system")
        self.assertTrue(value["api_key_set"])
        self.assertNotIn("fixture-legacy-key", response.text)
        persisted = self.stored()
        self.assertEqual(persisted.value, historical)
        self.assertEqual(persisted.updated_at, original.updated_at)

    def test_save_clears_legacy_model_and_keeps_same_provider_secret(self) -> None:
        self.seed({"mode": "provider", "provider": "deepseek",
                   "api_key": "fixture-retained-key", "model": "legacy-search-model"})
        value = self.put({"mode": "provider", "provider": "deepseek", "model": "old-client-model"})
        self.assertIsNone(value["model"])
        self.assertEqual(value["fetch_mode"], "system")
        self.assertTrue(value["api_key_set"])
        persisted = self.stored().value
        self.assertNotIn("model", persisted)
        self.assertEqual(persisted["api_key"], "fixture-retained-key")

    def test_old_client_omission_preserves_saved_follow_but_explicit_system_wins(self) -> None:
        for provider in ("deepseek", "kimi"):
            with self.subTest(provider=provider):
                self.put({"mode": "provider", "provider": provider,
                          "api_key": "fixture-follow-key", "fetch_mode": "follow_search"})
                value = self.put({"mode": "provider", "provider": provider})
                self.assertEqual(value["fetch_mode"], "follow_search")
                self.assertEqual(self.stored().value["fetch_mode"], "follow_search")
                self.assertEqual(self.stored().value["api_key"], "fixture-follow-key")
                value = self.put({"mode": "provider", "provider": provider, "fetch_mode": "system"})
                self.assertEqual(value["fetch_mode"], "system")

    def test_provider_reference_survives_legacy_model_cleanup_without_copying_key(self) -> None:
        provider = asyncio.run(self.stores.llm_configuration.create_provider(LlmProviderCreate(
            name="Fetch settings fixture", provider_type="deepseek", use_default_base_url=True,
            api_key="fixture-provider-key",
        )))
        self.seed({"mode": "provider", "provider": "deepseek", "model": "legacy-model",
                   "credential_source": "llm_provider", "llm_provider_id": provider.id})
        value = self.put({"mode": "provider", "provider": "deepseek",
                          "credential_source": "llm_provider", "fetch_mode": "follow_search"})
        self.assertEqual(value["llm_provider_id"], provider.id)
        self.assertTrue(value["api_key_set"])
        self.assertIsNone(value["api_key_masked"])
        persisted = self.stored().value
        self.assertEqual(persisted["llm_provider_id"], provider.id)
        self.assertNotIn("api_key", persisted)
        self.assertNotIn("model", persisted)
        self.assertEqual(asyncio.run(self.stores.llm_configuration.get_provider(provider.id)).api_key,
                         "fixture-provider-key")

    def test_unsupported_provider_and_clear_normalize_follow_to_system(self) -> None:
        self.put({"mode": "provider", "provider": "deepseek", "api_key": "fixture-key",
                  "fetch_mode": "follow_search"})
        for provider in ("tavily", "exa", "you", "duckduckgo"):
            with self.subTest(provider=provider):
                draft = {"mode": "provider", "provider": provider, "fetch_mode": "follow_search"}
                if provider != "duckduckgo":
                    draft["api_key"] = "fixture-vendor-key"
                value = self.put(draft)
                self.assertEqual(value["fetch_mode"], "system")
                self.assertEqual(self.stored().value["fetch_mode"], "system")
        self.assertEqual(self.put({"mode": "unconfigured"})["fetch_mode"], "system")
        self.assertEqual(self.stored().value, {})

    def test_connection_probe_uses_real_search_route_without_saving_or_fetch(self) -> None:
        existing = {"mode": "provider", "provider": "deepseek", "api_key": "fixture-stored-key",
                    "model": "keep-until-save"}
        self.seed(existing)
        sources = [{"title": "DeepFOS docs", "url": "https://docs.deepfos.com/", "snippet": "Documentation"}]
        for provider in ("deepseek", "kimi"):
            with self.subTest(provider=provider), \
                 patch("deep_agent.web.runtime.execute_deepseek_search", new=AsyncMock(return_value=sources)) as deepseek_search, \
                 patch("deep_agent.web.runtime.execute_search", new=AsyncMock(return_value={"search_results": sources})) as kimi_search, \
                 patch("deep_agent.web.runtime.search_client") as kimi_client, \
                 patch("deep_agent.web.runtime.execute_deepseek_fetch", new=AsyncMock()) as deepseek_fetch, \
                 patch("deep_agent.web.runtime.execute_kimi_fetch", new=AsyncMock()) as kimi_fetch:
                response = self.client.post(self.url + "/test", headers=self.headers, json={
                    "version": 1,
                    "value": {"mode": "provider", "provider": provider,
                              "api_key": "fixture-draft-key", "fetch_mode": "follow_search"},
                })
                self.assertEqual(response.status_code, 200, response.text)
                self.assertTrue(response.json()["ok"])
                self.assertEqual(response.json()["provider"], provider)
                self.assertNotIn("fixture-draft-key", response.text)
                if provider == "deepseek":
                    deepseek_search.assert_awaited_once()
                    self.assertEqual(deepseek_search.call_args.kwargs["api_key"], "fixture-draft-key")
                    kimi_search.assert_not_awaited()
                    kimi_client.assert_not_called()
                else:
                    kimi_search.assert_awaited_once()
                    kimi_client.assert_called_once_with("fixture-draft-key", None)
                    deepseek_search.assert_not_awaited()
                deepseek_fetch.assert_not_awaited()
                kimi_fetch.assert_not_awaited()
                self.assertEqual(self.stored().value, existing)
