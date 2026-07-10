"""Unit tests for models.dev catalog lookup."""

from __future__ import annotations

import unittest

from deep_agent.agent.models.models_dev import (
    clear_models_dev_cache,
    lookup_modalities_in_catalog,
)

SAMPLE_CATALOG = {
    "deepseek": {
        "models": {
            "deepseek-v4-flash": {
                "modalities": {"input": ["text"], "output": ["text"]},
                "limit": {"context": 1_000_000},
            }
        }
    },
    "moonshotai": {
        "models": {
            "kimi-k2.6": {
                "modalities": {"input": ["text", "image", "video"], "output": ["text"]},
                "limit": {"context": 262_144},
            }
        }
    },
    "openai": {
        "models": {
            "gpt-4o": {
                "modalities": {"input": ["text", "image", "pdf"], "output": ["text"]},
                "limit": {"context": 128_000},
            }
        }
    },
}


class ModelsDevLookupTests(unittest.TestCase):
    def tearDown(self) -> None:
        clear_models_dev_cache()

    def test_lookup_provider_prefixed_model(self) -> None:
        modalities = lookup_modalities_in_catalog(SAMPLE_CATALOG, "openai:gpt-4o")
        self.assertEqual(modalities, ["text", "image", "pdf"])

    def test_lookup_bare_model_with_base_url_hint(self) -> None:
        modalities = lookup_modalities_in_catalog(
            SAMPLE_CATALOG,
            "kimi-k2.6",
            "https://api.moonshot.cn/v1",
        )
        self.assertEqual(modalities, ["text", "image", "video"])

    def test_lookup_bare_deepseek_model(self) -> None:
        modalities = lookup_modalities_in_catalog(
            SAMPLE_CATALOG,
            "deepseek-v4-flash",
            "https://api.deepseek.com",
        )
        self.assertEqual(modalities, ["text"])


if __name__ == "__main__":
    unittest.main()
