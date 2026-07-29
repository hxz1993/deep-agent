"""Unit tests for models.dev normalization and database catalog views."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from deep_agent.agent.models.catalog import (
    CatalogValidationError,
    PROVIDER_ALLOWLIST,
    build_catalog_page,
    normalize_models_dev_bundle,
)
from deep_agent.db.models import LlmModel, ModelCatalog


MODELS = {
    "anthropic/claude-test": {
        "id": "anthropic/claude-test",
        "name": "Claude Test",
        "description": "Canonical description",
        "family": "claude",
        "modalities": {"input": ["text", "image"], "output": ["text"]},
        "limit": {"context": 200_000, "output": 8_192},
        "reasoning": True,
        "tool_call": True,
        "cost": {"input": 1, "output": 2},
        "release_date": "2026-01-01",
    },
    "openai/gpt-test": {
        "id": "openai/gpt-test",
        "name": "GPT Test",
        "release_date": "2025-01-01",
        "modalities": {"input": ["text"], "output": ["text"]},
        "limit": {"context": 128_000, "output": 16_384},
        "structured_output": True,
    },
}

PROVIDERS = {
    "anthropic": {
        "id": "anthropic",
        "name": "Anthropic",
        "npm": "@ai-sdk/anthropic",
        "api": "https://api.anthropic.com",
        "env": ["ANTHROPIC_API_KEY"],
        "models": {
            "claude-test": {
                "id": "anthropic/claude-test",
                "provider": {"api": "https://gateway.example/anthropic"},
                "limit": {"output": 4_096},
            }
        },
    },
    "openai": {
        "id": "openai",
        "name": "OpenAI",
        "npm": "@ai-sdk/openai",
        "api": "https://api.openai.com/v1",
        "models": {"gpt-test": {"id": "openai/gpt-test"}},
    },
    "openrouter": {
        "id": "openrouter",
        "name": "OpenRouter",
        "models": {"gpt-test": {"id": "openai/gpt-test"}},
    },
}

LABS = [
    {
        "type": "lab",
        "id": "anthropic",
        "name": "Anthropic",
        "description": "Anthropic Lab",
        "links": {"website": "https://anthropic.com"},
    }
]


class CatalogNormalizationTests(unittest.TestCase):
    def test_normalizes_three_dimensions_and_preserves_generic_metadata(self) -> None:
        rows, stats = normalize_models_dev_bundle(MODELS, PROVIDERS, LABS)
        self.assertEqual(stats.row_count, 2)
        self.assertEqual(stats.model_count, 2)
        self.assertEqual(stats.provider_count, 2)
        self.assertNotIn("openrouter", {row["provider_id"] for row in rows})

        claude = next(row for row in rows if row["provider_model_id"] == "claude-test")
        self.assertEqual(claude["lab_id"], "anthropic")
        self.assertEqual(claude["canonical_model_id"], "anthropic/claude-test")
        self.assertEqual(claude["canonical_max_output_tokens"], 8_192)
        self.assertEqual(claude["max_output_tokens"], 4_096)
        self.assertEqual(
            claude["provider_api_url"],
            "https://gateway.example/anthropic",
        )
        self.assertEqual(claude["input_modalities"], ["text", "image"])
        self.assertEqual(
            claude["capabilities"],
            {"reasoning": True, "tool_call": True},
        )
        self.assertEqual(claude["model_metadata"]["release_date"], "2026-01-01")
        self.assertEqual(claude["release_date"], "2026-01-01")
        self.assertEqual(
            claude["lab_metadata"]["links"],
            {"website": "https://anthropic.com"},
        )
        self.assertNotIn("cost", claude["model_metadata"])
        self.assertNotIn("cost", str(claude["provider_metadata"]))

    def test_allowlist_contains_only_supported_first_release_providers(self) -> None:
        self.assertEqual(
            PROVIDER_ALLOWLIST,
            {
                "alibaba",
                "anthropic",
                "deepseek",
                "google",
                "minimax",
                "moonshotai",
                "openai",
                "xai",
                "zhipuai",
            },
        )

    def test_invalid_complete_bundle_is_rejected(self) -> None:
        invalid = {
            "openai/gpt-test": {
                "id": "openai/gpt-test",
                "modalities": {"input": ["text"], "output": ["text"]},
                "limit": {"context": 8_192, "output": 16_384},
            }
        }
        with self.assertRaisesRegex(CatalogValidationError, "最大输出超过上下文窗口"):
            normalize_models_dev_bundle(invalid, PROVIDERS, LABS)

    def test_model_without_positive_token_limits_is_not_a_runtime_candidate(self) -> None:
        models = {
            **MODELS,
            "openai/image-test": {
                "id": "openai/image-test",
                "name": "Image Test",
                "modalities": {"input": ["text", "image"], "output": ["text", "image"]},
                "limit": {"context": 0, "output": 0},
            },
        }
        providers = {
            **PROVIDERS,
            "openai": {
                **PROVIDERS["openai"],
                "models": {
                    **PROVIDERS["openai"]["models"],
                    "image-test": {"id": "openai/image-test"},
                },
            },
        }

        rows, stats = normalize_models_dev_bundle(models, providers, LABS)

        self.assertEqual(stats.row_count, 2)
        self.assertNotIn(
            "image-test",
            {row["provider_model_id"] for row in rows},
        )

    def test_unsupported_non_text_output_is_filtered_before_token_validation(self) -> None:
        models = {
            **MODELS,
            "google/tts-test": {
                "id": "google/tts-test",
                "name": "TTS Test",
                "modalities": {"input": ["text"], "output": ["audio"]},
                "limit": {"context": 8_192, "output": 16_384},
            },
        }
        providers = {
            **PROVIDERS,
            "google": {
                "id": "google",
                "name": "Google",
                "models": {
                    "tts-test": {
                        "id": "google/tts-test",
                    },
                },
            },
        }

        rows, stats = normalize_models_dev_bundle(models, providers, LABS)

        self.assertEqual(stats.row_count, 2)
        self.assertNotIn(
            "tts-test",
            {row["provider_model_id"] for row in rows},
        )

    def test_combined_catalog_accepts_keyed_models_and_embedded_labs(self) -> None:
        keyed_model = dict(MODELS["openai/gpt-test"])
        keyed_model.pop("id")
        combined = {
            "models": {"openai/gpt-test": keyed_model},
            "labs": {
                "openai": {
                    "name": "OpenAI Research",
                    "description": "Embedded Lab metadata",
                }
            },
        }
        rows, stats = normalize_models_dev_bundle(
            combined,
            {"openai": PROVIDERS["openai"]},
            [],
        )
        self.assertEqual(stats.row_count, 1)
        self.assertEqual(rows[0]["canonical_model_id"], "openai/gpt-test")
        self.assertEqual(rows[0]["lab_label"], "OpenAI Research")
        self.assertEqual(rows[0]["lab_description"], "Embedded Lab metadata")


class CatalogViewTests(unittest.TestCase):
    def setUp(self) -> None:
        raw_rows, _ = normalize_models_dev_bundle(
            MODELS,
            PROVIDERS,
            LABS,
            refreshed_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        )
        self.rows = [ModelCatalog(**row) for row in raw_rows]
        self.configured = [
            LlmModel(
                id="model-1",
                name="Claude Preset",
                model="anthropic:claude-test",
                base_url="https://gateway.example/anthropic",
                input_modalities=["text", "image"],
                context_window_tokens=200_000,
                max_output_tokens=4_096,
                tags=["reasoning", "tool_call"],
            )
        ]

    def test_model_view_groups_provider_rows_and_marks_configured_preset(self) -> None:
        page = build_catalog_page(
            self.rows,
            self.configured,
            view="model",
            q="claude",
            lab_id=None,
            provider_id=None,
            modality="image",
            offset=0,
            limit=50,
        )
        self.assertEqual(page["total"], 1)
        item = page["items"][0]
        self.assertEqual(item["lab_label"], "Anthropic")
        self.assertEqual(item["provider_count"], 1)
        self.assertEqual(item["tags"], ["reasoning", "tool_call"])
        self.assertEqual(item["providers"][0]["configured_preset"], "Claude Preset")

    def test_model_results_are_sorted_by_release_date_descending(self) -> None:
        page = build_catalog_page(
            self.rows,
            [],
            view="model",
            q=None,
            lab_id=None,
            provider_id=None,
            modality=None,
            offset=0,
            limit=50,
        )
        self.assertEqual(
            [item["canonical_model_id"] for item in page["items"]],
            ["anthropic/claude-test", "openai/gpt-test"],
        )
        self.assertEqual(
            [item["release_date"] for item in page["items"]],
            ["2026-01-01", "2025-01-01"],
        )

    def test_provider_view_groups_models(self) -> None:
        page = build_catalog_page(
            self.rows,
            [],
            view="provider",
            q=None,
            lab_id=None,
            provider_id="openai",
            modality=None,
            offset=0,
            limit=50,
        )
        self.assertEqual(page["total"], 1)
        self.assertEqual(page["items"][0]["provider_id"], "openai")
        self.assertEqual(page["items"][0]["model_count"], 1)
        self.assertTrue(page["facets"]["labs"])


if __name__ == "__main__":
    unittest.main()
