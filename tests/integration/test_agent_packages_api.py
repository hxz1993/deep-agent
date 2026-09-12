"""Administrator package HTTP flows with real SQLite, local files, and runtime."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.agent_packages import archive
from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.files.storage import LocalFileStore
from deep_agent.security import ConsoleAdminAuthorizer
from deep_agent.skills.runtime import SkillRuntimeLibrary
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit.test_agent_packages import package_files


class AgentPackagesApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agent-package-api-TMP_to_delete-')
        self.root = Path(self.temp.name)
        self.stores, self.engine, factory = await make_sqlite_stores(f'sqlite+aiosqlite:///{self.root / "config.db"}')
        self.files = LocalFileStore(self.root / 'files')
        await self.files.initialize()
        self.runtime = SkillRuntimeLibrary(self.root / 'runtime', file_store=self.files)
        self.checker = AsyncMock(return_value=True)
        self.ctx = AppContext.for_testing(stores=self.stores, db_engine=self.engine,
            db_session_factory=factory, file_store=self.files, skill_runtime=self.runtime,
            console_admin_authorizer=ConsoleAdminAuthorizer(self.checker))
        init_context(self.ctx)
        app = FastAPI()
        app.include_router(api_router)
        self.client = AsyncClient(transport=ASGITransport(app=app), base_url='http://test')
        self.headers = {'user': 'package-admin', 'authorization': 'Bearer admin-test'}
        self.builtin = self.root / 'builtin-packages'
        self.builtin.mkdir()
        self.patcher = patch.object(archive, 'BUILTIN_PACKAGES_ROOT', self.builtin)
        self.patcher.start()
        self.raw = archive.build_zip(package_files(resources=True))

    async def asyncTearDown(self):
        self.patcher.stop()
        await self.client.aclose()
        reset_context()
        await self.ctx.aclose()
        self.temp.cleanup()

    def upload(self, raw=None):
        return {'file': ('agent.zip', raw or self.raw, 'application/zip')}

    def builtin_fixture(self, *, code='builtin-fixture', resources=True):
        (self.builtin / f'{code}.zip').write_bytes(archive.build_zip(package_files(resources=resources)))
        return code

    async def preview(self, **kwargs):
        result = await self.client.post('/api/v1/agent-packages/preview', headers=self.headers, **kwargs)
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()

    async def test_admin_permission_required_for_every_package_endpoint(self):
        self.checker.return_value = False
        ordinary = {'user': 'ordinary-user', 'authorization': 'Bearer ordinary-test'}
        calls = [('GET', '/api/v1/agent-packages'),
                 ('GET', '/api/v1/agents/00000000-0000-0000-0000-000000000001/export'),
                 ('POST', '/api/v1/agent-packages/preview'), ('POST', '/api/v1/agent-packages/install')]
        for method, path in calls:
            with self.subTest(path=path):
                denied = await self.client.request(method, path, headers=ordinary)
                self.assertEqual(denied.status_code, 403, denied.text)
                absent = await self.client.request(method, path)
                self.assertEqual(absent.status_code, 401, absent.text)
        self.assertEqual(await self.stores.agent.list_all(), [])

    async def test_upload_preview_install_export_and_reinstall_http_roundtrip(self):
        preview = await self.preview(files=self.upload())
        self.assertEqual(await self.stores.agent.list_all(), [])
        self.assertEqual(list(self.files.blob_root.rglob('*.blob')), [])
        options = {'digest': preview['digest'], 'name': 'HTTP上传安装验收', 'model_id': None}
        installed = await self.client.post('/api/v1/agent-packages/install', headers=self.headers,
            data={'options': json.dumps(options)}, files=self.upload())
        self.assertEqual(installed.status_code, 201, installed.text)
        body = installed.json()['agent']
        self.assertIsNone(body['model_id'])
        self.assertIsNone(body['model'])
        self.assertEqual(body['skills'], ['example-skill'])
        self.assertEqual(body['capabilities'], ['ui:package-fixture'])
        exported = await self.client.get(f'/api/v1/agents/{body["id"]}/export', headers=self.headers)
        self.assertEqual(exported.status_code, 200, exported.text[:100])
        self.assertEqual(exported.headers['content-type'], 'application/zip')
        self.assertIn('attachment;', exported.headers['content-disposition'])
        self.assertEqual(exported.headers['cache-control'], 'no-store')
        actual = archive.parse_package(exported.content)
        self.assertEqual(actual.agent.name, options['name'])
        second = await self.preview(files=self.upload(exported.content))
        self.assertEqual({r['status'] for r in second['resources']}, {'reuse'})
        options.update(digest=second['digest'], name='HTTP重新安装验收')
        again = await self.client.post('/api/v1/agent-packages/install', headers=self.headers,
            data={'options': json.dumps(options)}, files=self.upload(exported.content))
        self.assertEqual(again.status_code, 201, again.text)
        self.assertNotEqual(again.json()['agent']['id'], body['id'])
        self.assertEqual(len(await self.stores.agent.list_all()), 2)

    async def test_builtin_directory_listing_preview_and_install_use_same_pipeline(self):
        empty = await self.client.get('/api/v1/agent-packages', headers=self.headers)
        self.assertEqual(empty.status_code, 200)
        self.assertEqual(empty.json(), {'items': [], 'errors': []})
        code = self.builtin_fixture()
        (self.builtin / 'broken-package.zip').write_bytes(b'broken zip')
        (self.builtin / 'README.md').write_text('deployment packages')
        (self.builtin / 'ignored-folder').mkdir()
        (self.builtin / 'ignored-folder' / 'agent.json').write_text('{}')
        (self.builtin / 'not-a-file.zip').mkdir()
        listed = await self.client.get('/api/v1/agent-packages', headers=self.headers)
        self.assertEqual([i['code'] for i in listed.json()['items']], [code])
        self.assertEqual([i['code'] for i in listed.json()['errors']], ['broken-package'])
        preview = await self.preview(data={'builtin_code': code})
        uploaded = await self.preview(files=self.upload((self.builtin / f'{code}.zip').read_bytes()))
        self.assertEqual(preview['digest'], uploaded['digest'])
        result = await self.client.post('/api/v1/agent-packages/install', headers=self.headers,
            data={'builtin_code': code, 'options': json.dumps({'digest': preview['digest'], 'name': '自带安装验收'})})
        self.assertEqual(result.status_code, 201, result.text)
        self.assertTrue((self.runtime.root / 'example-skill/SKILL.md').is_file())

    async def test_builtin_change_between_preview_and_install_is_conflict(self):
        code = self.builtin_fixture(resources=False)
        preview = await self.preview(data={'builtin_code': code})
        path = self.builtin / f'{code}.zip'
        path.write_bytes(archive.build_zip(package_files(agent_changes={'prompt': 'changed after user preview'})))
        result = await self.client.post('/api/v1/agent-packages/install', headers=self.headers,
            data={'builtin_code': code, 'options': json.dumps({'digest': preview['digest'], 'name': '变化包'})})
        self.assertEqual(result.status_code, 409, result.text)
        self.assertEqual(await self.stores.agent.list_all(), [])

    async def test_builtin_link_and_oversize_are_rejected_without_installation(self):
        code = self.builtin_fixture(resources=False)
        (self.builtin / 'linked-fixture.zip').symlink_to(self.builtin / f'{code}.zip')
        listed = await self.client.get('/api/v1/agent-packages', headers=self.headers)
        self.assertNotIn('linked-fixture', [item['code'] for item in listed.json()['items']])
        linked = await self.client.post('/api/v1/agent-packages/preview', headers=self.headers,
            data={'builtin_code': 'linked-fixture'})
        self.assertEqual(linked.status_code, 422, linked.text)
        with patch.object(archive, 'MAX_PACKAGE_BYTES', 10):
            oversized = await self.client.post('/api/v1/agent-packages/preview', headers=self.headers,
                data={'builtin_code': code})
            self.assertEqual(oversized.status_code, 422, oversized.text)
        self.assertEqual(await self.stores.agent.list_all(), [])
        self.assertEqual(list(self.files.blob_root.rglob('*.blob')), [])

    async def test_invalid_source_or_format_returns_safe_422_without_writes(self):
        for kwargs in ({}, {'files': self.upload(), 'data': {'builtin_code': 'both'}},
                       {'files': self.upload(b'bad zip')}, {'data': {'builtin_code': '../outside'}}):
            with self.subTest(kwargs=kwargs.keys()):
                result = await self.client.post('/api/v1/agent-packages/preview', headers=self.headers, **kwargs)
                self.assertEqual(result.status_code, 422, result.text)
        self.assertEqual(await self.stores.agent.list_all(), [])
        self.assertEqual(list(self.files.blob_root.rglob('*.blob')), [])

    async def test_duplicate_name_reports_conflict_and_does_not_duplicate_resources(self):
        preview = await self.preview(files=self.upload())
        data = {'options': json.dumps({'digest': preview['digest'], 'name': '重名验收'})}
        first = await self.client.post('/api/v1/agent-packages/install', headers=self.headers, data=data, files=self.upload())
        self.assertEqual(first.status_code, 201, first.text)
        preview = await self.preview(files=self.upload())
        data['options'] = json.dumps({'digest': preview['digest'], 'name': '重名验收'})
        duplicate = await self.client.post('/api/v1/agent-packages/install', headers=self.headers, data=data, files=self.upload())
        self.assertEqual(duplicate.status_code, 409, duplicate.text)
        self.assertEqual(len(await self.stores.agent.list_all()), 1)
        self.assertEqual(len(list(self.files.blob_root.rglob('*.blob'))), 1)

    async def test_overwrite_mode_preserves_identity_and_rejects_stale_target(self):
        first=await self.preview(files=self.upload())
        installed=await self.client.post('/api/v1/agent-packages/install',headers=self.headers,files=self.upload(),
            data={'options':json.dumps({'digest':first['digest'],'name':'overwrite target','model_id':None})})
        self.assertEqual(installed.status_code,201,installed.text)
        target=installed.json()['agent']
        self.assertEqual(installed.json()['mode'],'create')
        changes=package_files(resources=True,agent_changes={'prompt':'New imported prompt'})
        payload=archive.build_zip(changes)
        source={'files':self.upload(payload),'data':{'mode':'overwrite','target_agent_id':target['id']}}
        preview=await self.preview(**source)
        options={'digest':preview['digest'],'name':'renamed target','mode':'overwrite','target_agent_id':target['id'],'model_id':None}
        # A target changed after preview must fail rather than apply an obsolete plan.
        from deep_agent.db.models import Agent
        async with self.stores.agent._write() as db:
            row=await db.get(Agent,target['id']);row.prompt='Concurrent edit'
        stale=await self.client.post('/api/v1/agent-packages/install',headers=self.headers,files=self.upload(payload),data={'options':json.dumps(options)})
        self.assertEqual(stale.status_code,409,stale.text)
        preview=await self.preview(**source);options['digest']=preview['digest']
        updated=await self.client.post('/api/v1/agent-packages/install',headers=self.headers,files=self.upload(payload),data={'options':json.dumps(options)})
        self.assertEqual(updated.status_code,201,updated.text)
        self.assertEqual(updated.json()['mode'],'overwrite')
        self.assertEqual(updated.json()['agent']['id'],target['id'])
        self.assertEqual(updated.json()['agent']['prompt'],'New imported prompt')
        self.assertEqual(len(await self.stores.agent.list_all()),1)

    async def test_invalid_modes_legacy_options_and_missing_targets_are_rejected(self):
        for fields in ({'mode':'unexpected'},{'mode':'overwrite'},{'mode':'create','target_agent_id':'missing'}):
            with self.subTest(fields=fields):
                response=await self.client.post('/api/v1/agent-packages/preview',headers=self.headers,files=self.upload(),data=fields)
                self.assertEqual(response.status_code,422,response.text)
        absent=await self.client.post('/api/v1/agent-packages/preview',headers=self.headers,files=self.upload(),
            data={'mode':'overwrite','target_agent_id':'00000000-0000-4000-8000-000000000999'})
        self.assertEqual(absent.status_code,409,absent.text)
        preview=await self.preview(files=self.upload())
        for extra in ({'resources':{}},{'mcp_mapping':{}}):
            with self.subTest(extra=extra):
                response=await self.client.post('/api/v1/agent-packages/install',headers=self.headers,files=self.upload(),
                    data={'options':json.dumps({'digest':preview['digest'],'name':'reject retired options',**extra})})
                self.assertEqual(response.status_code,422,response.text)
        self.assertEqual(await self.stores.agent.list_all(),[])
