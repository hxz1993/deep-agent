from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel

from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.ui.registry import prepare_ui_tool_packages_for_spec
from deep_agent.schemas.ui_tools import UiToolCreate, UiToolPackageCreate
from tests.unit import test_nullable_agent_model as nullable_fixtures


class AgentUiSelectionTests(unittest.TestCase):
    setUp = nullable_fixtures.NullableAgentApiTests.setUp
    tearDown = nullable_fixtures.NullableAgentApiTests.tearDown

    def prepare_packages(self):
        async def create():
            for code, names in (("ui:demo", ["demo_first", "demo_second"]), ("ui:other", ["other_first"])):
                await self.stores.ui_tool_package.create_manual(UiToolPackageCreate(code=code, label=code))
                for name in names:
                    await self.stores.ui_tool.create_manual(UiToolCreate(name=name, package_code=code, description=name, parameters_json_schema={"type": "object"}))
        asyncio.run(create())

    def test_api_round_trip_patch_pruning_and_ownership(self):
        self.prepare_packages()
        response = self.client.post('/api/v1/agents', json={
            'name': 'UI selection test', 'prompt': 'help', 'capabilities': ['ui:demo'],
            'ui_tool_selection': {'ui:demo': ['demo_first']},
        })
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()
        self.assertEqual(row['ui_tool_selection'], {'ui:demo': ['demo_first']})
        url = '/api/v1/agents/' + row['id']
        self.assertEqual(self.client.patch(url, json={'prompt': 'changed'}).json()['ui_tool_selection'], row['ui_tool_selection'])
        for selection in ({'ui:demo': ['other_first']}, {'ui:other': []}, {'ui:demo': ['missing_tool']}, {'ui:demo': ['demo_first', 'demo_first']}, None):
            response = self.client.patch(url, json={'ui_tool_selection': selection})
            self.assertEqual(response.status_code, 422, response.text)
        response = self.client.patch(url, json={'ui_tool_selection': {'ui:demo': []}})
        self.assertEqual(response.json()['ui_tool_selection'], {'ui:demo': []})
        response = self.client.patch(url, json={'capabilities': []})
        self.assertEqual(response.json()['ui_tool_selection'], {})
        response = self.client.patch(url, json={'capabilities': ['ui:demo']})
        self.assertEqual(response.json()['ui_tool_selection'], {})

    def test_store_rechecks_ownership_after_api_preview(self):
        from deep_agent.schemas.agent import AgentCreate
        self.prepare_packages()
        with self.assertRaisesRegex(ValueError, 'does not belong'):
            asyncio.run(self.stores.agent.create_unique(AgentCreate(
                name='Store scope', prompt='help', capabilities=['ui:demo'],
                ui_tool_selection={'ui:demo': ['other_first']},
            )))
        self.assertIsNone(asyncio.run(self.stores.agent.get_by_name('Store scope')))

    def test_configuration_import_rejects_invalid_scope(self):
        from sqlalchemy import update
        from deep_agent.db.models import Agent as AgentRow
        from deep_agent.schemas.agent import AgentCreate
        from deep_agent.services.configuration_import import _validate_logical_references, ConfigurationImportError
        self.prepare_packages()
        async def check():
            row = await self.stores.agent.create_unique(AgentCreate(name='Backup scope', prompt='help', capabilities=['ui:demo'], ui_tool_selection={'ui:demo':['demo_first']}))
            async with self.engine.begin() as connection:
                await _validate_logical_references(connection, prefix='Test backup')
                await connection.execute(update(AgentRow).where(AgentRow.id == row.id).values(ui_tool_selection={'ui:demo':['other_first']}))
                with self.assertRaisesRegex(ConfigurationImportError, 'invalid UI tool selections'):
                    await _validate_logical_references(connection, prefix='Test backup')
        asyncio.run(check())

    def test_runner_spec_retains_scope_with_model_and_capability_override(self):
        from tests.unit.test_session_model_override import _agent, _ctx, _llm
        from deep_agent.api.v1.deps import build_runner_for_agent
        async def check():
            ctx = _ctx()
            row = _agent(model_id="00000000-0000-4000-8000-000000000011")
            row.ui_tool_selection = {'ui:builtin': ['builtin__confirm']}
            ctx.stores.agent.get_by_id = AsyncMock(return_value=row)
            ctx.stores.llm_model.get_by_id = AsyncMock(return_value=_llm(row.model_id, "test"))
            with patch('deep_agent.api.v1.deps._build_runner_from_spec', new_callable=AsyncMock) as build:
                await build_runner_for_agent(ctx, row.id, capabilities=['ui:builtin'])
                self.assertEqual(build.call_args.args[0].ui_tool_selection, row.ui_tool_selection)
        asyncio.run(check())

    def test_runtime_whole_selected_empty_and_hitl_resume(self):
        self.prepare_packages()
        async def check():
            for scope, expected in (({}, {'demo_first','demo_second'}), ({'ui:demo':['demo_first']}, {'demo_first'}), ({'ui:demo':[]}, set())):
                prepared = await prepare_ui_tool_packages_for_spec(self.stores, AgentSpec(name='test', model='test', instructions='', capabilities=['ui:demo'], ui_tool_selection=scope))
                self.assertEqual(set(prepared[1]), expected)
            spec = AgentSpec(name='test', model='test', instructions='', capabilities=['ui:demo'], ui_tool_selection={'ui:demo':['demo_first']})
            prepared = await prepare_ui_tool_packages_for_spec(self.stores, spec)
            from deep_agent.agent.factory import _toolsets_for_spec
            from deep_agent.capabilities.registry import ToolPackageRegistry
            whole = await prepare_ui_tool_packages_for_spec(self.stores, AgentSpec(name='whole', model='test', instructions='', capabilities=['ui:demo']))
            registry = ToolPackageRegistry()
            for package in whole[0]:
                registry.register(package)
            for sets in (_toolsets_for_spec(registry, spec), _toolsets_for_spec(ToolPackageRegistry(), spec, dynamic_ui_packages=whole[0])):
                self.assertEqual(set((await sets[0].get_tools(None))), {'demo_first'})
            calls = 0
            def respond(messages, info):
                nonlocal calls
                calls += 1
                self.assertEqual({tool.name for tool in info.function_tools}, {'demo_first'})
                if calls == 1:
                    return ModelResponse(parts=[ToolCallPart('demo_first', {}, tool_call_id='scope-call')])
                return ModelResponse(parts=[TextPart('done')])
            agent = Agent(FunctionModel(respond), toolsets=[package.toolset for package in prepared[0]], output_type=[str, DeferredToolRequests])
            first = await agent.run('hello')
            self.assertIsInstance(first.output, DeferredToolRequests)
            result = await agent.run(message_history=first.all_messages(), deferred_tool_results=DeferredToolResults(calls={'scope-call':'ok'}))
            self.assertEqual(result.output, 'done')
        asyncio.run(check())
