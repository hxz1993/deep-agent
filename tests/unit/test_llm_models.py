from __future__ import annotations

import unittest

from pydantic import ValidationError
from sqlalchemy import Text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deep_agent.db.dialect import DbDialect
from deep_agent.db.models import Agent, Base, LlmModel
from deep_agent.db.stores.agent import create_agent_store
from deep_agent.db.stores.llm_model import (
    LlmModelValidationError,
    create_llm_model_store,
)
from deep_agent.db.types import JSONType
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.llm_model import LlmModelCreate, LlmModelUpdate


def model_create(name: str = "gpt-4o", **overrides) -> LlmModelCreate:
    data = {
        "name": name,
        "model": "openai:gpt-4o",
        "input_modalities": ["text"],
        "context_window_tokens": 128_000,
        "max_output_tokens": 16_384,
        "tags": [],
    }
    data.update(overrides)
    return LlmModelCreate(**data)


class LlmModelStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.store = create_llm_model_store(DbDialect.SQLITE, self.session_factory)
        self.agent_store = create_agent_store(DbDialect.SQLITE, self.session_factory)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_create_persists_runtime_facts_and_counts_agents(self) -> None:
        row = await self.store.create_unique(
            model_create(
                input_modalities=["text", "image"],
                tags=["reasoning", "tool_call"],
            )
        )
        self.assertEqual(row.input_modalities, ["text", "image"])
        self.assertEqual(row.context_window_tokens, 128_000)
        self.assertEqual(row.max_output_tokens, 16_384)
        self.assertEqual(row.tags, ["reasoning", "tool_call"])
        self.assertEqual(await self.store.count_agents(row.id), 0)

        await self.agent_store.create_unique(
            AgentCreate(name="a1", model_id=row.id, prompt="hi"),
        )
        self.assertEqual(await self.store.count_agents(row.id), 1)

    async def test_update_persists_runtime_facts(self) -> None:
        row = await self.store.create_unique(model_create())
        updated = await self.store.update_unique(
            row.id,
            LlmModelUpdate(tags=["reasoning"], max_output_tokens=8_192),
        )
        assert updated is not None
        self.assertEqual(updated.tags, ["reasoning"])
        self.assertEqual(updated.max_output_tokens, 8_192)

    async def test_update_rejects_invalid_merged_token_pair(self) -> None:
        row = await self.store.create_unique(model_create())
        with self.assertRaises(LlmModelValidationError):
            await self.store.update_unique(
                row.id,
                LlmModelUpdate(context_window_tokens=4_096),
            )

    def test_create_requires_modalities_and_token_limits(self) -> None:
        with self.assertRaises(ValidationError):
            LlmModelCreate(name="missing", model="private:model")
        with self.assertRaises(ValidationError):
            model_create(context_window_tokens=4_096, max_output_tokens=8_192)


class LlmModelMigrationShapeTests(unittest.TestCase):
    def test_agent_requires_model_id_column(self) -> None:
        cols = {column.name for column in Agent.__table__.columns}
        self.assertIn("model_id", cols)
        self.assertIn("temperature", cols)
        self.assertNotIn("model", cols)
        self.assertNotIn("base_url", cols)
        self.assertNotIn("api_key", cols)

    def test_llm_model_table_columns(self) -> None:
        self.assertEqual(
            {column.name for column in LlmModel.__table__.columns},
            {
                "id",
                "name",
                "description",
                "model",
                "base_url",
                "api_key",
                "input_modalities",
                "context_window_tokens",
                "max_output_tokens",
                "tags",
                "created_at",
                "updated_at",
            },
        )

    def test_json_fields_use_text_json_type(self) -> None:
        for field in ("input_modalities", "tags"):
            column = LlmModel.__table__.c[field]
            self.assertIsInstance(column.type, JSONType)
            self.assertIsInstance(column.type.impl, Text)


if __name__ == "__main__":
    unittest.main()
