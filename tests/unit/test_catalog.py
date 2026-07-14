"""Unit tests for model modality catalog."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from deep_agent.agent.models.catalog import (
    CatalogEntry,
    build_configured_preset_map,
    clear_model_catalog_cache,
    filter_catalog_entries,
    get_models_dev_entries,
    iter_bundled_entries,
    iter_models_dev_entries,
    list_models_dev_provider_ids,
    load_catalog_entries,
    lookup_configured_preset,
    normalize_model_lookup_key,
    paginate_catalog,
)

SAMPLE_CATALOG = {
    "openai": {
        "name": "OpenAI",
        "models": {
            "gpt-4o": {
                "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
                "limit": {"context": 128_000},
            },
            "gpt-4o-mini": {
                "modalities": {"input": ["text", "image"], "output": ["text"]},
            },
        },
    },
    "deepseek": {
        "models": {
            "deepseek-v4-flash": {
                "modalities": {"input": ["text"], "output": ["text"]},
            }
        }
    },
}


class CatalogHelpersTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_model_catalog_cache()

    def test_normalize_model_lookup_key(self) -> None:
        self.assertEqual(normalize_model_lookup_key("openai:gpt-4o"), "openai/gpt-4o")
        self.assertEqual(normalize_model_lookup_key("openai/gpt-4o"), "openai/gpt-4o")

    def test_build_configured_preset_map_full_key_only(self) -> None:
        m = build_configured_preset_map([("My GPT", "openai:gpt-4o"), ("Bare", "gpt-4o")])
        self.assertEqual(m["openai/gpt-4o"], "My GPT")
        self.assertNotIn("gpt-4o", m)

    def test_lookup_configured_preset(self) -> None:
        preset_map = build_configured_preset_map([("My GPT", "openai:gpt-4o")])
        entry = CatalogEntry(
            key="openai/gpt-4o",
            provider="openai",
            model_id="gpt-4o",
            provider_label="OpenAI",
            input_modalities=["text", "image"],
            max_context=None,
            source="models_dev",
        )
        self.assertEqual(lookup_configured_preset(preset_map, entry), "My GPT")

    def test_list_models_dev_provider_ids_without_parsing_models(self) -> None:
        self.assertEqual(list_models_dev_provider_ids(SAMPLE_CATALOG), ["deepseek", "openai"])

    def test_get_models_dev_entries_lazy_per_provider(self) -> None:
        clear_model_catalog_cache()
        openai_only = get_models_dev_entries(SAMPLE_CATALOG, provider="openai")
        self.assertEqual(len(openai_only), 2)
        self.assertTrue(all(e.provider == "openai" for e in openai_only))

    def test_iter_models_dev_entries(self) -> None:
        entries = iter_models_dev_entries(SAMPLE_CATALOG)
        self.assertEqual(len(entries), 3)
        gpt4o = next(e for e in entries if e.model_id == "gpt-4o")
        self.assertEqual(gpt4o.provider, "openai")
        self.assertEqual(gpt4o.provider_label, "OpenAI")
        self.assertIn("pdf", gpt4o.input_modalities)
        self.assertEqual(gpt4o.max_context, 128_000)

    def test_iter_bundled_entries_non_empty(self) -> None:
        entries = iter_bundled_entries()
        self.assertGreater(len(entries), 0)
        keys = {e.key.lower() for e in entries}
        self.assertIn("openai/gpt-4o", keys)

    def test_filter_and_paginate(self) -> None:
        entries = iter_models_dev_entries(SAMPLE_CATALOG)
        image_only = filter_catalog_entries(entries, modality="image")
        self.assertEqual(len(image_only), 2)
        page, total = paginate_catalog(image_only, offset=0, limit=1)
        self.assertEqual(total, 2)
        self.assertEqual(len(page), 1)


class CatalogLoadTests(unittest.IsolatedAsyncioTestCase):
    def tearDown(self) -> None:
        clear_model_catalog_cache()

    async def test_load_bundled_source(self) -> None:
        entries, resolved, providers = await load_catalog_entries(source="bundled")
        self.assertEqual(resolved, "bundled")
        self.assertGreater(len(entries), 0)
        self.assertGreater(len(providers), 0)

    async def test_load_online_from_mock_catalog(self) -> None:
        with patch(
            "deep_agent.agent.models.catalog.fetch_models_dev_catalog",
            new=AsyncMock(return_value=SAMPLE_CATALOG),
        ):
            entries, resolved, providers = await load_catalog_entries(source="online")
        self.assertEqual(resolved, "models_dev")
        self.assertEqual(len(entries), 3)
        self.assertEqual(providers, ["deepseek", "openai"])

    async def test_load_online_with_provider_only_parses_one_provider(self) -> None:
        with patch(
            "deep_agent.agent.models.catalog.fetch_models_dev_catalog",
            new=AsyncMock(return_value=SAMPLE_CATALOG),
        ):
            entries, resolved, providers = await load_catalog_entries(
                source="online",
                provider="openai",
            )
        self.assertEqual(resolved, "models_dev")
        self.assertEqual(len(entries), 2)
        self.assertEqual(providers, ["deepseek", "openai"])

    async def test_load_auto_falls_back_to_bundled(self) -> None:
        with patch(
            "deep_agent.agent.models.catalog.fetch_models_dev_catalog",
            new=AsyncMock(return_value=None),
        ):
            entries, resolved, _providers = await load_catalog_entries(source="auto")
        self.assertEqual(resolved, "bundled")
        self.assertGreater(len(entries), 0)

    async def test_refresh_clears_provider_cache(self) -> None:
        with patch(
            "deep_agent.agent.models.catalog.fetch_models_dev_catalog",
            new=AsyncMock(return_value=SAMPLE_CATALOG),
        ):
            await load_catalog_entries(source="online", provider="openai")
            entries, resolved, _providers = await load_catalog_entries(
                source="online",
                provider="openai",
                refresh=True,
            )
        self.assertEqual(resolved, "models_dev")
        self.assertEqual(len(entries), 2)


if __name__ == "__main__":
    unittest.main()
