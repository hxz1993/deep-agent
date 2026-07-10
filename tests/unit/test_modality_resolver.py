"""Unit tests for model modality resolution."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from deep_agent.agent.models.model_lookup import build_model_lookup_keys
from deep_agent.agent.models.modality_resolver import (
    _name_heuristic,
    refresh_model_modalities,
    resolve_input_modalities,
)
from deep_agent.db.dialect import DbDialect
from deep_agent.db.models import Base
from deep_agent.db.stores.llm_model import create_llm_model_store
from deep_agent.schemas.llm_model import LlmModelCreate
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

TEST_ENV_MODELS = (
    {
        "name": "deepseek-v4-flash",
        "model": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com",
        "expected": ["text"],
    },
    {
        "name": "kimi-k2.6",
        "model": "kimi-k2.6",
        "base_url": "https://api.moonshot.cn",
        "expected": ["text", "image", "video"],
    },
    {
        "name": "kimi-k2.7-code-highspeed",
        "model": "kimi-k2.7-code-highspeed",
        "base_url": "https://api.moonshot.cn",
        "expected": ["text", "image", "video"],
    },
)


class ModalityResolverTests(unittest.TestCase):
    def test_build_model_lookup_keys_uses_base_url_provider_hint(self) -> None:
        keys = build_model_lookup_keys("kimi-k2.6", "https://api.moonshot.cn/v1")
        self.assertIn("moonshotai/kimi-k2.6", keys)
        self.assertIn("kimi-k2.6", keys)

    def test_build_model_lookup_keys_keeps_provider_prefix_models(self) -> None:
        keys = build_model_lookup_keys("openai:gpt-4o")
        self.assertEqual(keys[:3], ["openai:gpt-4o", "openai/gpt-4o", "gpt-4o"])

    def test_test_env_models_resolve_from_bundle_without_provider_prefix(self) -> None:
        for case in TEST_ENV_MODELS:
            with self.subTest(name=case["name"]):
                resolved = resolve_input_modalities(case["model"], base_url=case["base_url"])
                self.assertEqual(resolved, case["expected"])

    def test_name_heuristic_falls_back_for_unknown_models(self) -> None:
        self.assertEqual(_name_heuristic("vendor:totally-unknown-model"), ["text"])


class ModalityRefreshTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.store = create_llm_model_store(DbDialect.SQLITE, self.session_factory)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_refresh_test_env_bare_models_populates_input_modalities(self) -> None:
        with patch(
            "deep_agent.agent.models.models_dev.fetch_models_dev_modalities",
            return_value=None,
        ):
            for case in TEST_ENV_MODELS:
                with self.subTest(name=case["name"]):
                    row = await self.store.create_unique(
                        LlmModelCreate(
                            name=case["name"],
                            model=case["model"],
                            base_url=case["base_url"],
                        )
                    )
                    result = await refresh_model_modalities(row.id, self.store, force=True)
                    self.assertEqual(result, case["expected"])
                    refreshed = await self.store.get_by_id(row.id)
                    assert refreshed is not None
                    self.assertEqual(refreshed.input_modalities, case["expected"])
                    self.assertEqual(refreshed.capabilities_source, "bundled")


if __name__ == "__main__":
    unittest.main()
