from __future__ import annotations

import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deep_agent.api.v1 import api_router
from deep_agent.api.v1.global_settings import build_global_settings
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.options import Agent as AgentOptions
from deep_agent.options import DeepAgentOptions
from tests.helpers.sqlite_stores import make_sqlite_stores_sync


def _find_item(response, key: str):
    for group in response.groups:
        for item in group.items:
            if item.key == key:
                return item
    raise AssertionError(f"Missing setting: {key}")


class GlobalSettingsBuilderTests(unittest.TestCase):
    def test_uses_default_value_and_marks_default_source(self) -> None:
        previous_source = AgentOptions.max_requests.set_by
        try:
            AgentOptions.max_requests.set_by = None
            with patch.dict(
                os.environ,
                {"DEEPFOS_COMPONENT_TEST_MODE": "true"},
                clear=False,
            ):
                settings = build_global_settings(DeepAgentOptions())
        finally:
            AgentOptions.max_requests.set_by = previous_source

        item = _find_item(settings, "max_requests")
        self.assertEqual(item.value, 40)
        self.assertEqual(item.display_value, "40")
        self.assertEqual(item.source, "default")
        self.assertEqual(item.env_name, "DEEP_AGENT_MAX_REQUESTS")

    def test_marks_configured_value_as_environment_source(self) -> None:
        previous_source = AgentOptions.max_requests.set_by
        try:
            with patch.dict(
                os.environ,
                {"DEEPFOS_COMPONENT_TEST_MODE": "true"},
                clear=False,
            ):
                options = DeepAgentOptions()
                options.agent.max_requests = 44
                AgentOptions.max_requests.set_by = "DEEP_AGENT_MAX_REQUESTS"
                settings = build_global_settings(options)
        finally:
            AgentOptions.max_requests.set_by = previous_source

        item = _find_item(settings, "max_requests")
        self.assertEqual(item.value, 44)
        self.assertEqual(item.source, "environment")

    def test_database_type_uses_effective_database_backend(self) -> None:
        with patch(
            "deep_agent.api.v1.global_settings.get_database_url",
            return_value="sqlite:////tmp/deep-agent.db",
        ):
            settings = build_global_settings()

        item = _find_item(settings, "database_type")
        self.assertEqual(item.value, "sqlite")
        self.assertEqual(item.display_value, "sqlite")

    def test_whitelist_does_not_expose_secrets_or_paths(self) -> None:
        settings = build_global_settings()
        payload = settings.model_dump_json().lower()
        for forbidden in (
            "deep_agent_dbms_password",
            "deep_agent_dbms_hostname",
            "deep_agent_dbms_user",
            "deep_agent_dbms_database",
            "deep_agent_gemini_proxy",
            "deep_agent_sqlite_path",
            "deep_agent_mcp_runtime_dir",
        ):
            self.assertNotIn(forbidden, payload)

        self.assertEqual(
            [group.key for group in settings.groups],
            [
                "runtime",
                "context_compaction",
                "mcp",
                "sandbox",
                "uploads",
                "images",
            ],
        )

    def test_sandbox_python_does_not_expose_interpreter_path(self) -> None:
        settings = build_global_settings()
        item = _find_item(settings, "sandbox_python")
        payload = settings.model_dump_json().lower()
        self.assertIn("deep_agent_sandbox_python", payload)
        self.assertEqual(item.display_value, "已配置")
        self.assertEqual(item.value, True)
        self.assertNotIn("/bin/", payload)
        self.assertNotIn("scripts/python", payload)

    def test_sandbox_memory_budget_displays_megabytes(self) -> None:
        settings = build_global_settings()
        item = _find_item(settings, "sandbox_memory_mb")
        self.assertEqual(item.unit, "MB")
        self.assertEqual(item.display_value, "512")
        self.assertEqual(item.value, 512)
        self.assertEqual(item.env_name, "DEEP_AGENT_SANDBOX_MEMORY_MB")


class GlobalSettingsApiTests(unittest.TestCase):
    def setUp(self) -> None:
        stores, self.engine, session_factory = make_sqlite_stores_sync()
        self.stores = stores
        init_context(
            AppContext.for_testing(
                stores=stores,
                db_engine=self.engine,
                db_session_factory=session_factory,
            )
        )
        app = FastAPI()
        app.include_router(api_router)
        self.client = TestClient(app)

    def tearDown(self) -> None:
        reset_context()
        asyncio.run(self.engine.dispose())

    def test_native_search_probe_is_removed_without_saving(self):
        response = self.client.post(
            "/api/v1/global-settings/web-search/test-model",
            headers={"user": "enterprise-admin"}, json={"model_id": "any-model"},
        )
        self.assertEqual(response.status_code, 410, response.text)
        self.assertIn("已移除", response.json()["detail"])
        self.assertIsNone(asyncio.run(self.stores.global_setting.get("web_search")))

    def test_returns_grouped_read_only_settings_for_enterprise_admin(
        self,
    ) -> None:
        response = self.client.get(
            "/api/v1/global-settings",
            headers={"user": "enterprise-admin"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["groups"])
        first = body["groups"][0]["items"][0]
        self.assertEqual(
            set(first),
            {
                "key",
                "label",
                "env_name",
                "value",
                "display_value",
                "source",
                "unit",
                "description",
                "restart_required",
            },
        )
        self.assertTrue(first["restart_required"])

    def test_web_search_defaults_to_unconfigured(self) -> None:
        response = self.client.get(
            "/api/v1/global-settings/web-search",
            headers={"user": "enterprise-admin"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["value"],
            {
                "mode": "unconfigured",
                "provider": None,
                "model": None,
                "api_key_set": False,
                "api_key_masked": None,
                "credential_source": None,
                "llm_provider_id": None,
                "llm_provider_name": None,
                "configuration_error": None,
            },
        )

    def test_provider_key_is_masked_and_retained_on_same_provider(self) -> None:
        headers = {"user": "enterprise-admin"}
        created = self.client.put(
            "/api/v1/global-settings/web-search",
            headers=headers,
            json={
                "version": 1,
                "value": {
                    "mode": "provider",
                    "provider": "deepseek",
                    "api_key": "deepseek-secret",
                },
            },
        )
        self.assertEqual(created.status_code, 200, created.text)
        self.assertTrue(created.json()["value"]["api_key_set"])
        self.assertNotIn("deepseek-secret", created.text)
        self.assertEqual(created.json()["value"]["model"], "deepseek-flash")

        retained = self.client.put(
            "/api/v1/global-settings/web-search",
            headers=headers,
            json={
                "version": 1,
                "value": {
                    "mode": "provider",
                    "provider": "deepseek",
                    "model": "deepseek-v4-pro",
                },
            },
        )
        self.assertEqual(retained.status_code, 200, retained.text)
        self.assertTrue(retained.json()["value"]["api_key_set"])
        self.assertEqual(retained.json()["value"]["model"], "deepseek-v4-pro")

    def test_switching_provider_does_not_reuse_previous_key(self) -> None:
        headers = {"user": "enterprise-admin"}
        first = self.client.put(
            "/api/v1/global-settings/web-search",
            headers=headers,
            json={
                "version": 1,
                "value": {
                    "mode": "provider",
                    "provider": "tavily",
                    "api_key": "tavily-secret",
                },
            },
        )
        self.assertEqual(first.status_code, 200, first.text)
        switched = self.client.put(
            "/api/v1/global-settings/web-search",
            headers=headers,
            json={
                "version": 1,
                "value": {"mode": "provider", "provider": "exa"},
            },
        )
        self.assertEqual(switched.status_code, 422, switched.text)
        self.assertIn("API key is required for exa", switched.text)

    def test_invalid_stored_configuration_can_be_replaced_and_draft_tested(self) -> None:
        headers = {"user": "enterprise-admin"}
        cases = (
            (1, {"mode": "broken"}),
            (2, {"mode": "native"}),
            (1, {"mode": "provider", "provider": "tavily"}),
            (1, {"mode": "provider", "provider": "deepseek",
                 "credential_source": "llm_provider", "llm_provider_id": "missing"}),
        )
        for version, value in cases:
            with self.subTest(value=value, version=version):
                asyncio.run(self.stores.global_setting.upsert("web_search", value=value, version=version))
                invalid = self.client.get("/api/v1/global-settings/web-search", headers=headers)
                self.assertEqual(invalid.status_code, 200, invalid.text)
                self.assertTrue(invalid.json()["value"]["configuration_error"])
                draft = {"version": 1, "value": {"mode": "provider", "provider": "tavily", "api_key": "new-test-key"}}
                with patch("deep_agent.api.v1.global_settings.probe_web_search_config", new=AsyncMock()) as probe:
                    tested = self.client.post("/api/v1/global-settings/web-search/test", headers=headers, json=draft)
                self.assertEqual(tested.status_code, 200, tested.text)
                self.assertEqual(probe.call_args.args[0].api_key, "new-test-key")
                self.assertEqual(asyncio.run(self.stores.global_setting.get("web_search")).value, value)
                saved = self.client.put("/api/v1/global-settings/web-search", headers=headers, json=draft)
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertEqual(asyncio.run(self.stores.global_setting.get("web_search")).value["provider"], "tavily")

    def test_invalid_old_credentials_do_not_allow_invalid_replacement(self) -> None:
        asyncio.run(self.stores.global_setting.upsert("web_search", value={"mode": "broken"}, version=1))
        response = self.client.put("/api/v1/global-settings/web-search", headers={"user": "enterprise-admin"},
                                   json={"version": 1, "value": {"mode": "provider", "provider": "tavily"}})
        self.assertEqual(response.status_code, 422, response.text)
        self.assertEqual(asyncio.run(self.stores.global_setting.get("web_search")).value, {"mode": "broken"})

    def test_duckduckgo_requires_no_key_or_model(self) -> None:
        response = self.client.put(
            "/api/v1/global-settings/web-search",
            headers={"user": "enterprise-admin"},
            json={
                "version": 1,
                "value": {
                    "mode": "provider",
                    "provider": "duckduckgo",
                    "api_key": "ignored",
                    "model": "ignored",
                },
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json()["value"],
            {
                "mode": "provider",
                "provider": "duckduckgo",
                "model": None,
                "api_key_set": False,
                "api_key_masked": None,
                "credential_source": None,
                "llm_provider_id": None,
                "llm_provider_name": None,
                "configuration_error": None,
            },
        )

    def test_deepseek_can_reuse_llm_provider_without_copying_its_key(self) -> None:
        headers = {"user": "enterprise-admin"}
        llm_provider = self.client.post(
            "/api/v1/llm-providers",
            headers=headers,
            json={
                "name": "DeepSeek Session",
                "provider_type": "deepseek",
                "use_default_base_url": True,
                "api_key": "shared-deepseek-secret",
            },
        )
        self.assertEqual(llm_provider.status_code, 201, llm_provider.text)
        provider_id = llm_provider.json()["id"]

        saved = self.client.put(
            "/api/v1/global-settings/web-search",
            headers=headers,
            json={
                "version": 1,
                "value": {
                    "mode": "provider",
                    "provider": "deepseek",
                    "model": "deepseek-v4-flash",
                    "credential_source": "llm_provider",
                    "llm_provider_id": provider_id,
                },
            },
        )
        self.assertEqual(saved.status_code, 200, saved.text)
        value = saved.json()["value"]
        self.assertEqual(value["credential_source"], "llm_provider")
        self.assertEqual(value["llm_provider_id"], provider_id)
        self.assertEqual(value["llm_provider_name"], "DeepSeek Session")
        self.assertTrue(value["api_key_set"])
        self.assertIsNone(value["api_key_masked"])
        self.assertNotIn("shared-deepseek-secret", saved.text)

        stored = asyncio.run(self.stores.global_setting.get("web_search"))
        self.assertEqual(stored.value["llm_provider_id"], provider_id)
        self.assertNotIn("api_key", stored.value)

    def test_reused_llm_provider_must_match_search_provider_type(self) -> None:
        headers = {"user": "enterprise-admin"}
        providers = self.client.get("/api/v1/llm-providers", headers=headers)
        self.assertEqual(providers.status_code, 200, providers.text)
        incompatible_id = providers.json()[0]["id"]
        response = self.client.put(
            "/api/v1/global-settings/web-search",
            headers=headers,
            json={
                "version": 1,
                "value": {
                    "mode": "provider",
                    "provider": "deepseek",
                    "credential_source": "llm_provider",
                    "llm_provider_id": incompatible_id,
                },
            },
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("must be deepseek", response.text)

    def test_all_keyed_providers_validate_only_their_required_fields(self) -> None:
        headers = {"user": "enterprise-admin"}
        cases = (
            ("deepseek", "deepseek-flash"),
            ("kimi", None),
            ("tavily", None),
            ("exa", None),
            ("you", None),
        )
        for provider, expected_model in cases:
            with self.subTest(provider=provider):
                missing = self.client.put(
                    "/api/v1/global-settings/web-search",
                    headers=headers,
                    json={
                        "version": 1,
                        "value": {"mode": "provider", "provider": provider},
                    },
                )
                self.assertEqual(missing.status_code, 422, missing.text)

                value = {
                    "mode": "provider",
                    "provider": provider,
                    "api_key": f"{provider}-secret",
                }
                if expected_model is None:
                    value["model"] = "ignored-neutral-model"
                saved = self.client.put(
                    "/api/v1/global-settings/web-search",
                    headers=headers,
                    json={
                        "version": 1,
                        "value": value,
                    },
                )
                self.assertEqual(saved.status_code, 200, saved.text)
                self.assertEqual(saved.json()["value"]["model"], expected_model)
                self.assertTrue(saved.json()["value"]["api_key_set"])
                self.assertNotIn(f"{provider}-secret", saved.text)

    def test_connectivity_test_uses_draft_without_saving(self) -> None:
        with patch(
            "deep_agent.api.v1.global_settings.probe_web_search_config",
            new=AsyncMock(return_value=None),
        ) as probe:
            response = self.client.post(
                "/api/v1/global-settings/web-search/test",
                headers={"user": "enterprise-admin"},
                json={
                    "version": 1,
                    "value": {
                        "mode": "provider",
                        "provider": "tavily",
                        "api_key": "draft-key",
                    },
                },
            )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["ok"])
        probe.assert_awaited_once()
        persisted = self.client.get(
            "/api/v1/global-settings/web-search",
            headers={"user": "enterprise-admin"},
        )
        self.assertEqual(persisted.json()["value"]["mode"], "unconfigured")

    def test_empty_and_legacy_settings_are_read_only_unconfigured(self):
        headers = {"user": "enterprise-admin"}
        for value in ({}, {"mode": "native"}):
            with self.subTest(value=value):
                asyncio.run(self.stores.global_setting.upsert("web_search", value=value, version=1))
                response = self.client.get("/api/v1/global-settings/web-search", headers=headers)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.json()["value"]["mode"], "unconfigured")
                self.assertEqual(asyncio.run(self.stores.global_setting.get("web_search")).value, value)

    def test_empty_native_or_implicit_write_and_test_are_rejected(self):
        headers = {"user": "enterprise-admin"}
        for value in ({}, {"mode": "native"}, {"provider": "duckduckgo"}):
            for method, path in ((self.client.put, ""), (self.client.post, "/test")):
                response = method("/api/v1/global-settings/web-search" + path,
                                  headers=headers, json={"value": value})
                self.assertEqual(response.status_code, 422, response.text)
        self.assertIsNone(asyncio.run(self.stores.global_setting.get("web_search")))

    def test_deleted_provider_reference_is_visible_and_repairable(self):
        value = {"mode": "provider", "provider": "kimi", "model": "kimi-k3",
                 "credential_source": "llm_provider", "llm_provider_id": "missing"}
        asyncio.run(self.stores.global_setting.upsert("web_search", value=value, version=1))
        headers = {"user": "enterprise-admin"}
        response = self.client.get("/api/v1/global-settings/web-search", headers=headers)
        actual = response.json()["value"]
        self.assertEqual(response.status_code, 200)
        self.assertEqual(actual["llm_provider_id"], "missing")
        self.assertEqual(actual["provider"], "kimi")
        self.assertIsNone(actual["model"])
        self.assertIn("no longer exists", actual["configuration_error"])
        self.assertEqual(asyncio.run(self.stores.global_setting.get("web_search")).value, value)
        response = self.client.put("/api/v1/global-settings/web-search", headers=headers,
                                  json={"value": {"mode": "provider", "provider": "kimi",
                                                  "credential_source": "api_key", "api_key": "new-key"}})
        self.assertEqual(response.status_code, 200, response.text)
        stored = asyncio.run(self.stores.global_setting.get("web_search"))
        self.assertNotIn("model", stored.value)
        self.assertNotIn("llm_provider_id", stored.value)

    def test_legacy_kimi_model_is_removed_only_when_saved(self):
        value = {"mode": "provider", "provider": "kimi", "model": "kimi-k3", "api_key": "old-key"}
        asyncio.run(self.stores.global_setting.upsert("web_search", value=value, version=1))
        headers = {"user": "enterprise-admin"}
        response = self.client.get("/api/v1/global-settings/web-search", headers=headers)
        self.assertIsNone(response.json()["value"]["model"])
        self.assertEqual(asyncio.run(self.stores.global_setting.get("web_search")).value, value)
        response = self.client.put("/api/v1/global-settings/web-search", headers=headers,
                                  json={"value": {"mode": "provider", "provider": "kimi", "model": "kimi-k3"}})
        self.assertEqual(response.status_code, 200, response.text)
        stored = asyncio.run(self.stores.global_setting.get("web_search"))
        self.assertNotIn("model", stored.value)
        self.assertEqual(stored.value["api_key"], "old-key")


if __name__ == "__main__":
    unittest.main()

    def test_clear_configuration_removes_credentials_and_allows_reconfiguration(self):
        headers = {"user": "enterprise-admin"}
        url = "/api/v1/global-settings/web-search"
        for existing in (
            {"mode": "provider", "provider": "kimi", "api_key": "fixture-secret", "model": "legacy"},
            {"mode": "provider", "provider": "deepseek", "credential_source": "llm_provider", "llm_provider_id": "deleted"},
        ):
            asyncio.run(self.stores.global_setting.upsert("web_search", value=existing, version=1))
            cleared = self.client.put(url, headers=headers, json={"value": {"mode": "unconfigured"}})
            self.assertEqual(cleared.status_code, 200, cleared.text)
            self.assertEqual(cleared.json()["value"]["mode"], "unconfigured")
            self.assertFalse(cleared.json()["value"]["api_key_set"])
            self.assertEqual(asyncio.run(self.stores.global_setting.get("web_search")).value, {})
            self.assertEqual(self.client.get(url, headers=headers).json()["value"]["mode"], "unconfigured")
            saved = self.client.put(url, headers=headers, json={"value": {"mode": "provider", "provider": "duckduckgo"}})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertEqual(saved.json()["value"]["provider"], "duckduckgo")

    def test_clear_rejects_mixed_provider_credentials(self):
        response = self.client.put("/api/v1/global-settings/web-search", headers={"user": "enterprise-admin"},
            json={"value": {"mode": "unconfigured", "api_key": "fixture-secret"}})
        self.assertEqual(response.status_code, 422)
        self.assertIsNone(asyncio.run(self.stores.global_setting.get("web_search")))
