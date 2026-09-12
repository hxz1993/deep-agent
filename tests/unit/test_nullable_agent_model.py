from __future__ import annotations

import asyncio
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from alembic import command
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from deep_agent.api.v1 import api_router
from deep_agent.api.v1.deps import build_runner_for_agent
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.core.exceptions import AgentModelNotConfiguredError
from deep_agent.db import migrate
from deep_agent.db.reference_schema import assert_logical_references
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores_sync
from tests.unit.test_session_model_override import _agent, _ctx, _llm


class NullableAgentApiTests(unittest.TestCase):
    def setUp(self):
        self.stores, self.engine, factory = make_sqlite_stores_sync()
        init_context(AppContext.for_testing(stores=self.stores, db_engine=self.engine, db_session_factory=factory))
        app = FastAPI()
        from tests.helpers.request_identity import install_test_user_header
        install_test_user_header(app)
        app.include_router(api_router)
        allow_console_admin(app)
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        reset_context()
        asyncio.run(self.engine.dispose())

    def test_temperature_removed_from_schema_and_rejected(self):
        from deep_agent.schemas.agent import AgentCreate, AgentUpdate, AgentResponse
        from deep_agent.schemas.chat import ChatRequest
        for schema in (AgentCreate, AgentUpdate, AgentResponse, ChatRequest):
            self.assertNotIn('temperature', schema.model_json_schema()['properties'])
        response = self.client.post('/api/v1/agents', json={'name': 'Temperature removed', 'prompt': 'Help', 'temperature': 0.5})
        self.assertEqual(response.status_code, 422, response.text)

    def test_create_omit_update_and_clear_model(self):
        response = self.client.post('/api/v1/agents', json={'name': 'No model', 'prompt': 'Help'})
        self.assertEqual(response.status_code, 201, response.text)
        agent = response.json()
        self.assertIsNone(agent['model_id'])
        self.assertIsNone(agent['model'])
        url = '/api/v1/agents/' + agent['id']
        model = asyncio.run(self.stores.llm_model.list_all())[0]
        response = self.client.patch(url, json={'model_id': model.id})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()['model']['id'], model.id)
        response = self.client.patch(url, json={'prompt': 'Changed'})
        self.assertEqual(response.json()['model_id'], model.id)
        response = self.client.patch(url, json={'model_id': None})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIsNone(response.json()['model_id'])
        self.assertIsNone(response.json()['model'])
        listing = self.client.get('/api/v1/agents').json()
        self.assertTrue(any(row['id'] == agent['id'] and row['model'] is None for row in listing))


    def test_chat_and_stream_reject_unconfigured_model_before_execution(self):
        agent = self.client.post('/api/v1/agents', json={'name': 'No model chat', 'prompt': 'Help', 'model_id': None}).json()
        for path in ('/api/v1/chat', '/api/v1/chat/stream'):
            with self.subTest(path=path), patch('deep_agent.api.v1.deps._build_runner_from_spec', new_callable=AsyncMock) as build:
                response = self.client.post(path, json={'agent_id': agent['id'], 'message': 'hello'})
                self.assertEqual(response.status_code, 422, response.text)
                self.assertEqual(response.json()['detail'], 'Agent 尚未配置模型')
                build.assert_not_awaited()


class NullableAgentRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_model_fails_before_loading_resources(self):
        ctx = _ctx()
        row = _agent(model_id=None)
        ctx.stores.agent.get_by_id = AsyncMock(return_value=row)
        with patch('deep_agent.api.v1.deps._build_runner_from_spec', new_callable=AsyncMock) as build:
            with self.assertRaisesRegex(AgentModelNotConfiguredError, 'Agent 尚未配置模型'):
                await build_runner_for_agent(ctx, row.id)
            build.assert_not_awaited()
            ctx.stores.llm_model.get_by_id.assert_not_awaited()
            ctx.stores.llm_configuration.get_provider.assert_not_awaited()

    async def test_request_model_works_without_agent_default(self):
        ctx = _ctx()
        row = _agent(model_id=None)
        model = _llm('00000000-0000-4000-8000-000000000011', 'override')
        ctx.stores.agent.get_by_id = AsyncMock(return_value=row)
        ctx.stores.llm_model.get_by_id = AsyncMock(return_value=model)
        with patch('deep_agent.api.v1.deps._build_runner_from_spec', new_callable=AsyncMock) as build:
            await build_runner_for_agent(ctx, row.id, model_id=model.id)
            self.assertEqual(build.call_args.args[0].model, 'openai:override')


class NullableAgentMigrationTests(unittest.TestCase):
    def test_upgrade_ddl_compiles_for_mysql_and_postgresql(self):
        from contextlib import redirect_stdout
        from io import StringIO
        for url in ('mysql+asyncmy://user:pass@127.0.0.1/db', 'postgresql+asyncpg://user:pass@127.0.0.1/db'):
            with self.subTest(url=url):
                config = migrate._build_alembic_config()
                config.attributes['database_url'] = url
                output = StringIO()
                with redirect_stdout(output):
                    command.upgrade(config, '004:005', sql=True)
                self.assertIn('ALTER TABLE agents', output.getvalue())
                self.assertIn('model_id', output.getvalue())

    def test_explicit_repair_of_unpublished_005_is_idempotent(self):
        import runpy
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        with tempfile.TemporaryDirectory(prefix='TMP_to_delete_v5_shape_') as directory:
            path = Path(directory) / 'db.sqlite'
            config = migrate._build_alembic_config()
            config.attributes['database_url'] = f'sqlite+aiosqlite:///{path}'
            command.upgrade(config, '005')
            with sqlite3.connect(path) as db:
                db.execute('ALTER TABLE agents ADD COLUMN temperature FLOAT')
                db.execute('ALTER TABLE agents DROP COLUMN ui_tool_selection')
                db.execute('UPDATE agents SET temperature=0.5')
                before = db.execute('SELECT id,name,prompt,model_id FROM agents ORDER BY id').fetchall()
            engine = create_engine(f'sqlite:///{path}')
            try:
                for _ in range(2):
                    with engine.begin() as connection:
                        self.assertEqual(connection.scalar(__import__('sqlalchemy').text('SELECT version_num FROM alembic_version')), '005')
                        with Operations.context(MigrationContext.configure(connection)):
                            runpy.run_path('migrations/versions/005_nullable_agent_model.py')['upgrade']()
                with sqlite3.connect(path) as db:
                    self.assertEqual(db.execute('SELECT version_num FROM alembic_version').fetchone()[0], '005')
                    self.assertNotIn('temperature', {row[1] for row in db.execute('PRAGMA table_info(agents)')})
                    self.assertEqual(before, db.execute('SELECT id,name,prompt,model_id FROM agents ORDER BY id').fetchall())
                    self.assertTrue(all(row[0] == '{}' for row in db.execute('SELECT ui_tool_selection FROM agents')))
                    self.assertEqual(next(row[3] for row in db.execute('PRAGMA table_info(agents)') if row[1] == 'ui_tool_selection'), 1)
            finally:
                engine.dispose()

    def test_repair_partial_nullable_selection_column_preserves_existing_scopes(self):
        import runpy
        from alembic.migration import MigrationContext
        from alembic.operations import Operations
        with tempfile.TemporaryDirectory(prefix='TMP_to_delete_v5_partial_scope_') as directory:
            path = Path(directory) / 'db.sqlite'
            config = migrate._build_alembic_config()
            config.attributes['database_url'] = f'sqlite+aiosqlite:///{path}'
            command.upgrade(config, '005')
            with sqlite3.connect(path) as db:
                db.execute('ALTER TABLE agents DROP COLUMN ui_tool_selection')
                db.execute('ALTER TABLE agents ADD COLUMN ui_tool_selection TEXT')
                first_id = db.execute('SELECT id FROM agents ORDER BY id LIMIT 1').fetchone()[0]
                db.execute('UPDATE agents SET ui_tool_selection=? WHERE id=?', ('{"ui:builtin":[]}', first_id))
                # Add a NULL row even if future initial defaults contain one Agent.
                db.execute("INSERT INTO agents (id,name,model_id,capabilities,skills,prompt) SELECT 'partial-agent', 'Partial migration Agent',model_id,capabilities,skills,prompt FROM agents LIMIT 1")
                before = db.execute('SELECT id,name,model_id,prompt FROM agents ORDER BY id').fetchall()
            engine = create_engine(f'sqlite:///{path}')
            try:
                for _ in range(2):
                    with engine.begin() as connection:
                        with Operations.context(MigrationContext.configure(connection)):
                            runpy.run_path('migrations/versions/005_nullable_agent_model.py')['upgrade']()
                with sqlite3.connect(path) as db:
                    self.assertEqual(db.execute('SELECT version_num FROM alembic_version').fetchone()[0], '005')
                    self.assertEqual(next(row[3] for row in db.execute('PRAGMA table_info(agents)') if row[1] == 'ui_tool_selection'), 1)
                    self.assertEqual(db.execute('SELECT ui_tool_selection FROM agents WHERE id=?', (first_id,)).fetchone()[0], '{"ui:builtin":[]}')
                    self.assertEqual(db.execute("SELECT ui_tool_selection FROM agents WHERE id='partial-agent'").fetchone()[0], '{}')
                    self.assertEqual(before, db.execute('SELECT id,name,model_id,prompt FROM agents ORDER BY id').fetchall())
            finally:
                engine.dispose()

    def test_upgrade_preserves_data_and_downgrade_requires_models(self):
        with tempfile.TemporaryDirectory(prefix='TMP_to_delete_nullable_agent_') as directory:
            path = Path(directory) / 'db.sqlite'
            url = f'sqlite+aiosqlite:///{path}'
            with patch('deep_agent.db.migrate.get_database_url', return_value=url):
                config = migrate._build_alembic_config()
            config.attributes['database_url'] = url
            command.upgrade(config, '004')
            with sqlite3.connect(path) as db:
                before = db.execute('SELECT id,name,model_id,prompt FROM agents ORDER BY id').fetchall()
            command.upgrade(config, '005')
            with sqlite3.connect(path) as db:
                self.assertEqual(before, db.execute('SELECT id,name,model_id,prompt FROM agents ORDER BY id').fetchall())
                self.assertEqual(next(row[3] for row in db.execute('PRAGMA table_info(agents)') if row[1] == 'model_id'), 0)
                self.assertNotIn('temperature', {row[1] for row in db.execute('PRAGMA table_info(agents)')})
                db.execute('UPDATE agents SET model_id=NULL WHERE id=?', (before[0][0],))
            engine = create_engine(f'sqlite:///{path}')
            try:
                with engine.connect() as connection:
                    assert_logical_references(connection)
            finally:
                engine.dispose()
            with self.assertRaisesRegex(RuntimeError, 'configure their models first'):
                command.downgrade(config, '004')
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT version_num FROM alembic_version').fetchone()[0], '005')
                self.assertEqual(db.execute('SELECT COUNT(*) FROM agents WHERE model_id IS NULL').fetchone()[0], 1)
                db.execute('UPDATE agents SET model_id=? WHERE id=?', (before[0][2], before[0][0]))
            with sqlite3.connect(path) as db:
                db.execute('UPDATE agents SET ui_tool_selection=? WHERE id=?', ('{"ui:builtin": []}', before[0][0]))
            with self.assertRaisesRegex(RuntimeError, 'UI tool selections exist'):
                command.downgrade(config, '004')
            with sqlite3.connect(path) as db:
                self.assertEqual(db.execute('SELECT version_num FROM alembic_version').fetchone()[0], '005')
                db.execute("UPDATE agents SET ui_tool_selection='{}'")
            command.downgrade(config, '004')
            with sqlite3.connect(path) as db:
                self.assertEqual(next(row[3] for row in db.execute('PRAGMA table_info(agents)') if row[1] == 'model_id'), 1)
                self.assertEqual(db.execute('SELECT COUNT(*) FROM agents WHERE temperature IS NOT NULL').fetchone()[0], 0)
                self.assertEqual(before, db.execute('SELECT id,name,model_id,prompt FROM agents ORDER BY id').fetchall())


class NullableAgentChannelTests(unittest.IsolatedAsyncioTestCase):
    async def test_client_only_recognizes_safe_missing_model_error(self):
        from types import SimpleNamespace
        from unittest.mock import MagicMock
        from deep_agent.channels.gateway.deepagent_client import DeepAgentChannelClient
        for raw, expected in ((b'{"detail":"other private exception"}', RuntimeError),
                              ('{"detail":"Agent 尚未配置模型"}'.encode(), AgentModelNotConfiguredError)):
            client = DeepAgentChannelClient(SimpleNamespace(api_url='http://localhost'))
            client.start = AsyncMock()
            client._headers = MagicMock(return_value={})
            client._session = MagicMock()
            response = SimpleNamespace(status=422, read=AsyncMock(return_value=raw))
            client._session.post.return_value.__aenter__ = AsyncMock(return_value=response)
            client._session.post.return_value.__aexit__ = AsyncMock(return_value=False)
            with self.assertRaises(expected) as caught:
                async for _ in client.stream_chat(SimpleNamespace(agent_id='agent'), session_id='test', message='hello', attachments=[]):
                    pass
            self.assertNotIn('private', str(caught.exception))


from tests.unit import test_channel_gateway as gateway_fixtures
from tests.unit import test_channel_commands as command_fixtures


class NullableAgentChannelDeliveryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = gateway_fixtures.ChannelGatewayTests.asyncSetUp
    asyncTearDown = gateway_fixtures.ChannelGatewayTests.asyncTearDown
    message = command_fixtures.ChannelCommandTests.message
    wait_idle = command_fixtures.ChannelCommandTests.wait_idle

    async def test_unconfigured_model_is_actionable_in_channel_reply(self):
        from deep_agent.channels.service import ChannelService
        agent, adapter = command_fixtures.FakeAgent(), command_fixtures.FakeAdapter()
        async def stream(*args, **kwargs):
            raise AgentModelNotConfiguredError(self.account.agent_id)
            yield  # asynchronous stream contract
        agent.stream_chat = stream
        service = ChannelService(self.store, agent)
        try:
            await service.receive(self.account, adapter, self.message('fixture'))
            await self.wait_idle(service)
            self.assertIn('Agent 尚未配置模型', adapter.replies[-1][1])
        finally:
            await service.close_account(self.account.id)
