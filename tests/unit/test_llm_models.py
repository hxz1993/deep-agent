from __future__ import annotations

import unittest

from pydantic import ValidationError
from sqlalchemy import Text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deep_agent.agent.spec import AgentSpec
from deep_agent.db.dialect import DbDialect
from deep_agent.db.models import Agent, AuxiliaryModelConfig, Base, LlmModel, LlmProvider
from deep_agent.db.stores.agent import create_agent_store
from deep_agent.db.stores.llm_model import (
    LlmModelValidationError,
    create_llm_model_store,
)
from deep_agent.db.types import JSONType
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.llm_model import (
    LlmModelCreate,
    LlmModelResponse,
    LlmModelUpdate,
)
from deep_agent.services.llm_configuration import (
    LlmConfigurationConflictError,
    LlmConfigurationService,
)

_PROVIDER_ID = "1" * 32


def model_create(name: str = "gpt-4o", **overrides) -> LlmModelCreate:
    data = {
        "name": name,
        "provider_id": _PROVIDER_ID,
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
        self.service = LlmConfigurationService(self.session_factory)
        self.agent_store = create_agent_store(DbDialect.SQLITE, self.session_factory)
        async with self.session_factory.begin() as session:
            session.add(
                LlmProvider(
                    id=_PROVIDER_ID,
                    name="Test Provider",
                    provider_type="openai_compatible",
                    use_default_base_url=False,
                    base_url="http://test.invalid/v1",
                    api_key="test-key",
                    enabled=True,
                )
            )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_create_persists_runtime_facts_and_counts_agents(self) -> None:
        row = await self.service.create_model(
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
        row = await self.service.create_model(model_create())
        updated = await self.service.update_model(
            row.id,
            LlmModelUpdate(tags=["reasoning"], max_output_tokens=8_192),
        )
        assert updated is not None
        self.assertEqual(updated.tags, ["reasoning"])
        self.assertEqual(updated.max_output_tokens, 8_192)

    async def test_reference_count_includes_auxiliary_model_configuration(self) -> None:
        row = await self.service.create_model(model_create())
        async with self.session_factory.begin() as session:
            session.add(
                AuxiliaryModelConfig(
                    use_case="session_title",
                    llm_model_id=row.id,
                )
            )

        self.assertEqual(await self.store.count_agents(row.id), 0)
        self.assertEqual(await self.store.count_references(row.id), 1)
        with self.assertRaises(LlmConfigurationConflictError):
            await self.service.delete_model(row.id)
        self.assertIsNotNone(await self.store.get_by_id(row.id))

    async def test_historical_pdf_modality_is_hidden_from_runtime_and_api(self) -> None:
        row = await self.service.create_model(model_create())
        row.input_modalities = ["text", "image", "pdf"]

        self.assertEqual(
            AgentSpec.from_llm_model(row, provider=await self._provider()).input_modalities,
            ["text", "image"],
        )
        self.assertEqual(
            LlmModelResponse.from_row(
                row,
                provider=await self._provider(),
            ).input_modalities,
            ["text", "image"],
        )

    async def test_update_rejects_invalid_merged_token_pair(self) -> None:
        row = await self.service.create_model(model_create())
        with self.assertRaises(LlmModelValidationError):
            await self.service.update_model(
                row.id,
                LlmModelUpdate(context_window_tokens=4_096),
            )

    async def test_model_names_are_unique_within_provider_only(self) -> None:
        first = await self.service.create_model(model_create())
        with self.assertRaises(LlmConfigurationConflictError):
            await self.service.create_model(model_create(model="openai:gpt-4.1"))

        second_provider_id = "2" * 32
        async with self.session_factory.begin() as session:
            session.add(
                LlmProvider(
                    id=second_provider_id,
                    name="Second Provider",
                    provider_type="openai_compatible",
                    use_default_base_url=False,
                    base_url="http://second.invalid/v1",
                    api_key="test-key",
                    enabled=True,
                )
            )
        second = await self.service.create_model(
            model_create(provider_id=second_provider_id)
        )
        self.assertNotEqual(first.id, second.id)

    def test_create_requires_modalities_and_token_limits(self) -> None:
        with self.assertRaises(ValidationError):
            LlmModelCreate(name="missing", model="private:model")
        with self.assertRaises(ValidationError):
            model_create(context_window_tokens=4_096, max_output_tokens=8_192)

    async def _provider(self) -> LlmProvider:
        async with self.session_factory() as session:
            provider = await session.get(LlmProvider, _PROVIDER_ID)
            assert provider is not None
            return provider


class LlmModelMigrationShapeTests(unittest.TestCase):
    def test_provider_ids_use_compact_uuid_storage(self) -> None:
        self.assertEqual(LlmProvider.__table__.c.id.type.length, 32)
        self.assertEqual(LlmModel.__table__.c.provider_id.type.length, 32)

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
                "provider_id",
                "enabled",
                "input_modalities",
                "context_window_tokens",
                "max_output_tokens",
                "tags",
                "created_at",
                "updated_at",
            },
        )

    def test_provider_reference_is_a_restricting_foreign_key(self) -> None:
        foreign_key = next(iter(LlmModel.__table__.c.provider_id.foreign_keys))
        self.assertEqual(foreign_key.target_fullname, "llm_providers.id")
        self.assertEqual(foreign_key.ondelete, "RESTRICT")

    def test_model_name_uniqueness_is_scoped_to_provider(self) -> None:
        constraints = {
            constraint.name: tuple(column.name for column in constraint.columns)
            for constraint in LlmModel.__table__.constraints
            if constraint.name
        }
        self.assertEqual(
            constraints["uq_llm_models_provider_name"],
            ("provider_id", "name"),
        )

    def test_json_fields_use_text_json_type(self) -> None:
        for field in ("input_modalities", "tags"):
            column = LlmModel.__table__.c[field]
            self.assertIsInstance(column.type, JSONType)
            self.assertIsInstance(column.type.impl, Text)


if __name__ == "__main__":
    unittest.main()
