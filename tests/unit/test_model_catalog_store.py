from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deep_agent.db.dialect import DbDialect
from deep_agent.db.models import Base
from deep_agent.db.stores.model_catalog import create_model_catalog_store


def catalog_row(provider_model_id: str = "gpt-test") -> dict:
    return {
        "provider_id": "openai",
        "provider_model_id": provider_model_id,
        "provider_label": "OpenAI",
        "provider_npm": "@ai-sdk/openai",
        "provider_api_url": "https://api.openai.com/v1",
        "provider_doc_url": None,
        "lab_id": "openai",
        "lab_label": "OpenAI",
        "lab_description": None,
        "canonical_model_id": f"openai/{provider_model_id}",
        "model_label": provider_model_id,
        "model_description": None,
        "family": "gpt",
        "release_date": "2026-01-01",
        "canonical_context_window_tokens": 128_000,
        "canonical_max_output_tokens": 8_192,
        "context_window_tokens": 128_000,
        "max_output_tokens": 8_192,
        "input_modalities": ["text"],
        "output_modalities": ["text"],
        "capabilities": {"tool_call": True},
        "status": "active",
        "lab_metadata": {},
        "model_metadata": {},
        "provider_metadata": {"adapter": "openai"},
        "source": "models_dev",
        "refreshed_at": datetime(2026, 7, 28, tzinfo=timezone.utc),
    }


class ModelCatalogStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.store = create_model_catalog_store(DbDialect.SQLITE, factory)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_replace_all_is_atomic(self) -> None:
        old = catalog_row("old-model")
        await self.store.replace_all([old])

        duplicate = catalog_row("duplicate")
        with self.assertRaises(IntegrityError):
            await self.store.replace_all([duplicate, duplicate])

        rows = await self.store.list_all()
        self.assertEqual(
            [(row.provider_id, row.provider_model_id) for row in rows],
            [("openai", "old-model")],
        )


if __name__ == "__main__":
    unittest.main()
