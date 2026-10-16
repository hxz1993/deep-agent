"""Run with isolated databases only; opt-in server URLs must name a *_fk_test DB."""
from __future__ import annotations
import asyncio
import os
import unittest
from unittest.mock import patch

from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart, TextPart
from sqlalchemy import inspect, select, text
from deep_agent.db.models import Base, Agent, ChatSession, ChatSessionEvent, LlmModel, LlmProvider
from deep_agent.db.factory import create_engine
from deep_agent.db.dialect import resolve_dialect
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.db.reference_integrity import ReferenceConflict, acquire_write_barrier
from deep_agent.schemas.agent import AgentCreate, AgentUpdate
from deep_agent.schemas.llm_model import LlmModelCreate, LlmModelUpdate
from deep_agent.schemas.llm_provider import LlmProviderCreate
from deep_agent.schemas.ui_tools import UiToolCreate, UiToolUpdate, UiToolPackageCreate
from deep_agent.services.llm_configuration import LlmConfigurationService


class ReferenceBehavior:
    url = 'sqlite+aiosqlite:///:memory:'

    async def asyncSetUp(self):
        self.engine, self.factory = create_engine(self.url)
        # Exercise the deployed schema, including dialect-specific defaults.
        # Remove only this explicitly isolated test schema before migrations.
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
            await conn.execute(text('DROP TABLE IF EXISTS alembic_version'))
        from alembic import command
        from deep_agent.db.migrate import _build_alembic_config
        config = _build_alembic_config()
        config.attributes['database_url'] = self.url
        if self.engine.dialect.name == 'sqlite':
            # In-memory engines cannot share Alembic's separate connection.
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
        else:
            await asyncio.to_thread(command.upgrade, config, 'head')
            async with self.engine.begin() as conn:
                for table in Base.metadata.tables.values():
                    await conn.execute(table.delete())
        self.stores = create_data_stores(resolve_dialect(self.url), self.factory)
        self.service = LlmConfigurationService(self.factory)
        self.provider = await self.service.create_provider(LlmProviderCreate(name='Provider', provider_type='openai_compatible', use_default_base_url=False, base_url='http://example.invalid/v1'))
        self.model = await self.service.create_model(self.model_data())

    async def asyncTearDown(self):
        await self.engine.dispose()

    def model_data(self, **kw):
        return LlmModelCreate(**dict(provider_id=self.provider.id, name='Model', model='test', input_modalities=['text'], context_window_tokens=1024, max_output_tokens=128) | kw)

    async def agent(self, **kw):
        return await self.stores.agent.create_unique(AgentCreate(**dict(name='Agent', model_id=self.model.id, prompt='你好 👋📊🙂') | kw))

    async def assert_integrity(self):
        from deep_agent.db.reference_schema import assert_logical_references
        async with self.engine.connect() as conn:
            await conn.run_sync(assert_logical_references)
            fks = await conn.run_sync(lambda c: sum(len(inspect(c).get_foreign_keys(t)) for t in Base.metadata.tables))
            self.assertEqual(fks, 0)

    async def test_provider_model_agent_validation_delete_and_unique(self):
        with self.assertRaises(ValueError):
            await self.service.create_model(self.model_data(provider_id='f'*32))
        with self.assertRaises(ValueError):
            await self.agent(model_id='missing')
        agent = await self.agent()
        with self.assertRaises(ValueError):
            await self.agent()
        with self.assertRaises(ValueError):
            await self.stores.agent.update_unique(agent.id, AgentUpdate(model_id='missing'))
        with self.assertRaises(ValueError):
            await self.service.delete_model(self.model.id)
        with self.assertRaises(ValueError):
            await self.service.delete_provider(self.provider.id)
        provider2 = await self.service.create_provider(LlmProviderCreate(name='P2', provider_type='openai_compatible', use_default_base_url=False, base_url='http://example.invalid/v1'))
        moved = await self.service.update_model(self.model.id, LlmModelUpdate(provider_id=provider2.id))
        self.assertEqual(moved.provider_id, provider2.id)
        self.assertTrue(await self.service.delete_provider(self.provider.id))
        self.assertEqual((await self.stores.agent.get_by_id(agent.id)).llm_model.id, self.model.id)
        self.assertTrue(await self.stores.agent.delete(agent.id))
        self.assertTrue(await self.service.delete_provider(provider2.id))
        self.assertIsNone(await self.stores.llm_model.get_by_id(self.model.id))
        await self.assert_integrity()

    async def test_ui_tool_package_references(self):
        for code in ('ui:first', 'ui:second'):
            await self.stores.ui_tool_package.create_manual(UiToolPackageCreate(code=code, label=code))
        def tool(code):
            return UiToolCreate(name='example_tool', package_code=code, description='test', parameters_json_schema={'type':'object','properties':{}}, widget='test')
        with self.assertRaises(ValueError):
            await self.stores.ui_tool.create_manual(tool('ui:missing'))
        await self.stores.ui_tool.create_manual(tool('ui:first'))
        first_agent = await self.agent(capabilities=['ui:first'])
        with self.assertRaises(ValueError):
            await self.stores.ui_tool_package.delete('ui:first')
        await self.stores.agent.delete(first_agent.id)
        with self.assertRaises(ValueError):
            await self.stores.ui_tool.update('example_tool', UiToolUpdate(package_code='ui:missing'))
        updated = await self.stores.ui_tool.update('example_tool', UiToolUpdate(package_code='ui:second'))
        self.assertEqual(updated.package.code, 'ui:second')
        self.assertTrue(await self.stores.ui_tool_package.delete('ui:first'))
        agent = await self.agent(capabilities=['ui:second'])
        with self.assertRaises(ValueError):
            await self.stores.ui_tool.delete('example_tool')
        await self.stores.agent.delete(agent.id)
        self.assertTrue(await self.stores.ui_tool_package.delete('ui:second'))
        self.assertIsNone(await self.stores.ui_tool.get_by_name('example_tool'))
        await self.assert_integrity()

    async def test_session_history_unicode_delete_and_late_event(self):
        a = await self.agent()
        co = self.stores.coordinator
        messages = [ModelRequest(parts=[UserPromptPart('你好 👋📊🙂')]), ModelResponse(parts=[TextPart('中文 👋📊🙂')])]
        with self.assertRaises(ValueError):
            await co.append('invalid', messages, agent_id='missing', user_id='u', run_id='r', turn_id='t')
        await co.append('session', messages, agent_id=a.id, user_id='u', run_id='r', turn_id='t')
        self.assertEqual((await co.get_history('session', agent_id=a.id))[1].parts[0].content, '中文 👋📊🙂')
        with self.assertRaises(ValueError):
            await self.stores.agent.delete(a.id)
        async with self.factory() as db:
            detached = await db.get(ChatSession, 'session')
        await co.delete('session', agent_id=a.id)
        self.assertEqual(await co.list_events('session'), [])
        with self.assertRaises(ReferenceConflict):
            async with co._write() as db:
                await co.events._append_messages(db, detached, messages, run_id='late', turn_id='late')
        await self.stores.agent.delete(a.id)
        await self.assert_integrity()

    async def test_create_agent_races_model_delete(self):
        results = await asyncio.gather(self.agent(), self.service.delete_model(self.model.id), return_exceptions=True)
        self.assertEqual(sum(isinstance(x, ValueError) for x in results), 1, results)
        await self.assert_integrity()

    async def test_create_model_races_provider_delete(self):
        results = await asyncio.gather(self.service.create_model(self.model_data(name='Another')), self.service.delete_provider(self.provider.id), return_exceptions=True)
        self.assertFalse(any(isinstance(x, BaseException) and not isinstance(x, ValueError) for x in results), results)
        await self.assert_integrity()

    async def test_new_session_races_agent_delete(self):
        a = await self.agent()
        results = await asyncio.gather(self.stores.coordinator.append('s', [ModelRequest(parts=[UserPromptPart('hello')])], agent_id=a.id,user_id='u',run_id='r',turn_id='t'),self.stores.agent.delete(a.id), return_exceptions=True)
        self.assertEqual(sum(isinstance(x, ValueError) for x in results), 1, results)
        await self.assert_integrity()

    async def test_parent_lock_blocks_reference_creation(self):
        if self.engine.dialect.name == 'sqlite':
            return  # real server behavior is exercised by MySQL/Postgres classes
        from deep_agent.db.reference_integrity import lock_reference
        async with self.factory() as db:
            async with db.begin():
                await acquire_write_barrier(db)
                await lock_reference(db, LlmModel, LlmModel.id, self.model.id, 'Model')
                task = asyncio.create_task(self.agent())
                try:
                    await asyncio.sleep(.15)
                    self.assertFalse(task.done(), 'another connection must wait for the parent row lock')
                finally:
                    await db.commit()
                    await asyncio.wait_for(task, 5)
        await self.assert_integrity()

    async def test_configuration_import_barrier_excludes_writes(self):
        if self.engine.dialect.name == 'sqlite':
            return
        async with self.engine.connect() as conn:
            async with conn.begin():
                await acquire_write_barrier(conn, exclusive=True)
                task = asyncio.create_task(self.agent())
                try:
                    await asyncio.sleep(.15)
                    self.assertFalse(task.done())
                finally:
                    await conn.commit()
                    await asyncio.wait_for(task, 5)
        await self.assert_integrity()


class SQLiteLogicalReferences(ReferenceBehavior, unittest.IsolatedAsyncioTestCase):
    pass


@unittest.skipUnless(os.environ.get('DEEPAGENT_FK_MYSQL_URL'), 'isolated MySQL URL not provided')
class MySQLLogicalReferences(ReferenceBehavior, unittest.IsolatedAsyncioTestCase):
    url = os.environ.get('DEEPAGENT_FK_MYSQL_URL', '')
    async def asyncSetUp(self):
        from sqlalchemy.engine import make_url
        parsed = make_url(self.url)
        if parsed.database != 'runtime_fk_test' or parsed.host not in {'127.0.0.1', 'localhost', '::1'}:
            raise RuntimeError('Refusing destructive tests outside runtime_fk_test')
        await super().asyncSetUp()


@unittest.skipUnless(os.environ.get('DEEPAGENT_FK_POSTGRES_URL'), 'isolated PostgreSQL URL not provided')
class PostgresLogicalReferences(ReferenceBehavior, unittest.IsolatedAsyncioTestCase):
    url = os.environ.get('DEEPAGENT_FK_POSTGRES_URL', '')
    async def asyncSetUp(self):
        from sqlalchemy.engine import make_url
        parsed = make_url(self.url)
        if parsed.database != 'runtime_fk_test' or parsed.host not in {'127.0.0.1', 'localhost', '::1'}:
            raise RuntimeError('Refusing destructive tests outside runtime_fk_test')
        await super().asyncSetUp()
