from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from deep_agent.web.settings import load_web_search_config, resolve_web_search_config


class WebSearchSettingsTests(unittest.IsolatedAsyncioTestCase):
    async def _load(self, value, *, version=1, provider=None):
        row = SimpleNamespace(value=value, version=version)
        store = SimpleNamespace(get=AsyncMock(return_value=row))
        llm = SimpleNamespace(get_provider=AsyncMock(return_value=provider))
        return await load_web_search_config(store, llm)

    async def test_missing_empty_and_native_do_not_resolve_or_write(self):
        llm = SimpleNamespace(get_provider=AsyncMock())
        for row in (None, SimpleNamespace(value={}, version=1),
                    SimpleNamespace(value={"mode": "native"}, version=1)):
            store = SimpleNamespace(get=AsyncMock(return_value=row))
            config = await load_web_search_config(store, llm)
            self.assertEqual(config.mode, "unconfigured")
            self.assertIsNone(config.configuration_error)
            self.assertIsNone(config.provider)
        llm.get_provider.assert_not_awaited()

    async def test_invalid_settings_are_runtime_failures_not_load_errors(self):
        for value in ([], "invalid", {"mode": "unknown"}, {"mode": "provider", "provider": "unknown"}):
            with self.subTest(value=value):
                config = await self._load(value)
                self.assertIsNotNone(config.configuration_error)
        config = await self._load({"mode": "provider", "provider": "kimi"})
        self.assertEqual(config.provider, "kimi")
        self.assertIn("API key", config.configuration_error)
        config = await self._load({}, version=2)
        self.assertIn("version", config.configuration_error)

    async def test_broken_references_remain_editable_and_safe(self):
        value = {"mode": "provider", "provider": "kimi", "credential_source": "llm_provider",
                 "llm_provider_id": "provider-1"}
        cases = (
            None,
            SimpleNamespace(name="Wrong", provider_type="deepseek", api_key="secret"),
            SimpleNamespace(name="Kimi", provider_type="moonshotai", api_key=None),
            SimpleNamespace(name="Kimi", provider_type="moonshotai", api_key="secret",
                            use_default_base_url=False, base_url="https://user:secret@example.com"),
            SimpleNamespace(name="Kimi", provider_type="moonshotai", api_key="secret",
                            use_default_base_url=False, base_url="https://["),
        )
        for provider in cases:
            with self.subTest(provider=provider):
                config = await self._load(value, provider=provider)
                self.assertEqual(config.llm_provider_id, "provider-1")
                self.assertEqual(config.provider, "kimi")
                self.assertTrue(config.configuration_error)
                self.assertNotIn("secret", config.configuration_error)
                self.assertNotIn("configuration_error", config.to_stored_value())

    async def test_reference_resolves_latest_key_and_preserves_base(self):
        value = {"mode": "provider", "provider": "kimi", "credential_source": "llm_provider",
                 "llm_provider_id": "provider-1", "model": "kimi-k3"}
        provider = SimpleNamespace(id="provider-1", name="Kimi", provider_type="moonshotai",
                                   api_key="latest-secret", use_default_base_url=False,
                                   base_url="https://api.moonshot.cn/v1")
        config = await self._load(value, provider=provider)
        self.assertEqual(config.api_key, "latest-secret")
        self.assertEqual(config.base_url, "https://api.moonshot.cn/v1")
        self.assertIsNone(config.model)
        self.assertIsNone(config.configuration_error)
        self.assertNotIn("model", config.to_stored_value())
        self.assertNotIn("api_key", config.to_stored_value())
        self.assertEqual(value["model"], "kimi-k3")

    def test_deepseek_default_and_explicit_models(self):
        value = {"mode": "provider", "provider": "deepseek", "api_key": "key"}
        self.assertEqual(resolve_web_search_config(value).model, "deepseek-flash")
        value["model"] = "custom-future-model"
        self.assertEqual(resolve_web_search_config(value).model, "custom-future-model")

    async def test_storage_infrastructure_errors_are_not_hidden(self):
        store = SimpleNamespace(get=AsyncMock(side_effect=RuntimeError("database unavailable")))
        with self.assertRaisesRegex(RuntimeError, "database unavailable"):
            await load_web_search_config(store, None)
