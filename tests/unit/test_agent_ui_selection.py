from __future__ import annotations

import asyncio
import unittest

from sqlalchemy import update

from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities.ui.registry import prepare_ui_tool_packages_for_spec
from deep_agent.db.models import Agent as AgentRow
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.ui_tools import UiToolCreate, UiToolPackageCreate
from deep_agent.services.configuration_import import _validate_logical_references
from tests.unit import test_nullable_agent_model as nullable_fixtures


class AgentUiPackageTests(unittest.TestCase):
    setUp = nullable_fixtures.NullableAgentApiTests.setUp
    tearDown = nullable_fixtures.NullableAgentApiTests.tearDown

    def prepare_packages(self):
        async def create():
            await self.stores.ui_tool_package.create_manual(UiToolPackageCreate(code="ui:demo", label="Demo"))
            for name in ("demo_first", "demo_second"):
                await self.stores.ui_tool.create_manual(UiToolCreate(
                    name=name, package_code="ui:demo", description=name,
                    parameters_json_schema={"type": "object"},
                ))
        asyncio.run(create())

    def test_legacy_api_scope_is_ignored_and_next_save_clears_legacy_column(self):
        self.prepare_packages()
        response = self.client.post('/api/v1/agents', json={
            'name': 'UI package test', 'prompt': 'help', 'capabilities': ['ui:demo'],
            'ui_tool_selection': {'ui:demo': ['demo_first']},
        })
        self.assertEqual(response.status_code, 201, response.text)
        row = response.json()
        self.assertNotIn('ui_tool_selection', row)

        async def seed_and_read():
            async with self.engine.begin() as db:
                await db.execute(update(AgentRow).where(AgentRow.id == row['id']).values(
                    ui_tool_selection={'ui:demo': []},
                ))
            return (await self.stores.agent.get_by_id(row['id'])).ui_tool_selection

        self.assertEqual(asyncio.run(seed_and_read()), {'ui:demo': []})
        changed = self.client.patch('/api/v1/agents/' + row['id'], json={
            'prompt': 'changed', 'ui_tool_selection': {'ui:demo': []},
        })
        self.assertEqual(changed.status_code, 200, changed.text)
        self.assertNotIn('ui_tool_selection', changed.json())
        self.assertEqual(asyncio.run(self.stores.agent.get_by_id(row['id'])).ui_tool_selection, {})

    def test_historical_scope_does_not_restrict_runtime_or_backup_validation(self):
        self.prepare_packages()

        async def check():
            row = await self.stores.agent.create_unique(AgentCreate(
                name='Historical scope', prompt='help', capabilities=['ui:demo'],
            ))
            async with self.engine.begin() as db:
                await db.execute(update(AgentRow).where(AgentRow.id == row.id).values(
                    ui_tool_selection={'ui:demo': ['demo_first']},
                ))
                await _validate_logical_references(db, prefix='Old backup')
            refreshed = await self.stores.agent.get_by_id(row.id)
            packages, hints = await prepare_ui_tool_packages_for_spec(self.stores, AgentSpec(
                name='test', model='test', instructions='', capabilities=refreshed.capabilities,
            ))
            self.assertEqual(set(hints), {'demo_first', 'demo_second'})
            self.assertEqual(len(packages), 1)

        asyncio.run(check())
