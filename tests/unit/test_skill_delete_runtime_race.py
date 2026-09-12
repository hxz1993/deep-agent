from __future__ import annotations

import shutil
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from deep_agent.api.v1.skills import _record, delete_skill
from deep_agent.skills.package import parse_skill_zip
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit.test_skill_packages import skill_zip


class SkillDeleteRuntimeRaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, _ = await make_sqlite_stores()
        parsed = parse_skill_zip(skill_zip(name='delete-runtime-fixture'))
        try:
            self.record = _record(parsed, platform_file_id='old-file', file_name='fixture.zip', file_size=1)
        finally:
            shutil.rmtree(parsed.package_root.parent, ignore_errors=True)
        await self.stores.skill_package.create(self.record)
        self.runtime = SimpleNamespace(remove=AsyncMock())
        self.files = SimpleNamespace(delete=AsyncMock())
        self.ctx = SimpleNamespace(stores=self.stores, skill_runtime=self.runtime, file_store=self.files)
        self.request = Request({'type': 'http', 'headers': []})

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_late_delete_cleanup_keeps_reinstalled_runtime(self):
        original_delete = self.stores.skill_package.delete

        async def delete_then_reinstall(code):
            result = await original_delete(code)
            await self.stores.skill_package.create(self.record.model_copy(update={'platform_file_id': 'new-file'}))
            return result

        with patch.object(self.stores.skill_package, 'delete', side_effect=delete_then_reinstall):
            response = await delete_skill(self.record.code, self.request, self.ctx)
        self.assertEqual(response['status'], 'deleted')
        row = await self.stores.skill_package.get_by_code(self.record.code)
        self.assertEqual(row.platform_file_id, 'new-file')
        self.runtime.remove.assert_not_awaited()
        self.assertEqual(self.files.delete.await_args.args[0], 'old-file')

    async def test_unreplaced_skill_removes_runtime_and_original_file(self):
        await delete_skill(self.record.code, self.request, self.ctx)
        self.assertIsNone(await self.stores.skill_package.get_by_code(self.record.code))
        self.runtime.remove.assert_awaited_once_with(self.record.code)
        self.assertEqual(self.files.delete.await_args.args[0], 'old-file')

    async def test_stale_api_preflight_cannot_delete_new_agent_reference(self):
        from fastapi import HTTPException
        from deep_agent.schemas.agent import AgentCreate
        await self.stores.agent.create_unique(AgentCreate(name='Installed meanwhile', prompt='test', skills=[self.record.code]))
        with patch.object(self.stores.agent, 'list_referencing_skill', new=AsyncMock(return_value=[])):
            with self.assertRaises(HTTPException) as error:
                await delete_skill(self.record.code, self.request, self.ctx)
        self.assertEqual(error.exception.status_code, 409)
        self.assertIsNotNone(await self.stores.skill_package.get_by_code(self.record.code))
        self.runtime.remove.assert_not_awaited()
        self.files.delete.assert_not_awaited()
