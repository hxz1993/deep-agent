from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from deep_agent.db.dialect import DbDialect
from deep_agent.db.models import Agent, Base, LlmModel
from deep_agent.db.stores.agent import create_agent_store
from deep_agent.db.stores.llm_model import create_llm_model_store
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.llm_model import LlmModelCreate


class LlmModelStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.store = create_llm_model_store(DbDialect.SQLITE, self.session_factory)
        self.agent_store = create_agent_store(DbDialect.SQLITE, self.session_factory)

    async def test_create_and_count_agents(self) -> None:
        row = await self.store.create_unique(
            LlmModelCreate(name="gpt-4o", model="openai:gpt-4o"),
        )
        self.assertTrue(row.id)
        count = await self.store.count_agents(row.id)
        self.assertEqual(count, 0)

        await self.agent_store.create_unique(
            AgentCreate(name="a1", model_id=row.id, prompt="hi"),
        )
        self.assertEqual(await self.store.count_agents(row.id), 1)

    async def test_create_with_modalities_sets_capabilities_source_user(self) -> None:
        row = await self.store.create_unique(
            LlmModelCreate(
                name="vision",
                model="private:vision",
                input_modalities=["text", "image"],
            ),
        )
        self.assertEqual(row.capabilities_source, "user")
        self.assertEqual(row.input_modalities, ["text", "image"])

    async def test_create_without_modalities_leaves_source_empty(self) -> None:
        row = await self.store.create_unique(
            LlmModelCreate(name="plain", model="openai:gpt-4o"),
        )
        self.assertIsNone(row.capabilities_source)
        self.assertIsNone(row.input_modalities)

    async def test_delete_blocked_when_referenced(self) -> None:
        row = await self.store.create_unique(
            LlmModelCreate(name="shared", model="openai:gpt-4o"),
        )
        await self.agent_store.create_unique(
            AgentCreate(name="a1", model_id=row.id, prompt="hi"),
        )
        # ponytail: FK RESTRICT on DB; store delete would fail at flush in real DB.
        self.assertEqual(await self.store.count_agents(row.id), 1)

    async def test_update_metadata_persists_modalities(self) -> None:
        row = await self.store.create_unique(
            LlmModelCreate(name="refresh-target", model="openai:gpt-4o"),
        )
        updated = await self.store.update_metadata(
            row.id,
            input_modalities=["text", "image"],
            capabilities_source="models_dev",
        )
        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.input_modalities, ["text", "image"])
        self.assertEqual(updated.capabilities_source, "models_dev")

        fetched = await self.store.get_by_id(row.id)
        assert fetched is not None
        self.assertEqual(fetched.input_modalities, ["text", "image"])

    async def test_update_modalities_mode_auto_clears_manual_override(self) -> None:
        from deep_agent.schemas.llm_model import LlmModelUpdate

        row = await self.store.create_unique(
            LlmModelCreate(
                name="manual-model",
                model="openai:gpt-4o",
                input_modalities=["text", "image"],
            ),
        )
        self.assertEqual(row.capabilities_source, "user")

        updated = await self.store.update_unique(row.id, LlmModelUpdate(modalities_mode="auto"))
        assert updated is not None
        self.assertIsNone(updated.input_modalities)
        self.assertIsNone(updated.capabilities_source)


class LlmModelMigrationShapeTests(unittest.TestCase):
    def test_agent_requires_model_id_column(self) -> None:
        cols = {c.name for c in Agent.__table__.columns}
        self.assertIn("model_id", cols)
        self.assertIn("temperature", cols)
        self.assertNotIn("model", cols)
        self.assertNotIn("base_url", cols)
        self.assertNotIn("api_key", cols)

    def test_llm_model_table_columns(self) -> None:
        cols = {c.name for c in LlmModel.__table__.columns}
        self.assertEqual(
            cols,
            {
                "id",
                "name",
                "description",
                "model",
                "base_url",
                "api_key",
                "input_modalities",
                "capabilities_source",
                "created_at",
                "updated_at",
            },
        )

    def test_input_modalities_uses_text_json_type(self) -> None:
        """ORM stores JSON as TEXT; Alembic must match (not native JSON on Postgres)."""
        from sqlalchemy import Text

        from deep_agent.db.types import JSONType

        col = LlmModel.__table__.c.input_modalities
        self.assertIsInstance(col.type, JSONType)
        self.assertIsInstance(col.type.impl, Text)


if __name__ == "__main__":
    unittest.main()
