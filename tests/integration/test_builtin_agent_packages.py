"""Shipped builtin ZIP packages use the same installation flow as uploads."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.agent_packages import archive
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.files.storage import LocalFileStore
from deep_agent.skills.runtime import RUNTIME_READY, SkillRuntimeLibrary
from tests.helpers.sqlite_stores import make_sqlite_stores


class BuiltinAgentPackagesTests(unittest.IsolatedAsyncioTestCase):
    async def test_shipped_packages_list_preview_install_and_preserve_all_runtime_files(self):
        with tempfile.TemporaryDirectory(prefix='builtin-agent-packages-TMP_to_delete-') as temp:
            root = Path(temp)
            stores, engine, factory = await make_sqlite_stores(f'sqlite+aiosqlite:///{root / "config.db"}')
            files = LocalFileStore(root / 'files')
            await files.initialize()
            runtime = SkillRuntimeLibrary(root / 'runtime', file_store=files)
            ctx = AppContext.for_testing(stores=stores, db_engine=engine, db_session_factory=factory,
                file_store=files, skill_runtime=runtime, registry=build_registry())
            init_context(ctx)
            app = FastAPI()
            app.include_router(api_router)
            headers = {'user': 'builtin-package-test', 'authorization': 'Bearer test-only'}
            try:
                async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test', headers=headers) as client:
                    listing = await client.get('/api/v1/agent-packages')
                    self.assertEqual(listing.status_code, 200, listing.text)
                    self.assertEqual(listing.json()['errors'], [])
                    self.assertEqual({item['code'] for item in listing.json()['items']}, {'deeptable-assistant', 'deepcube-assistant'})
                    self.assertEqual(await stores.agent.list_all(), [])
                    for code, expected_skills, tool_count in (
                        ('deeptable-assistant', {'deeptable-assistant-workflows', 'deeptable-intelligent-analysis'}, 3),
                        ('deepcube-assistant', {'deepcube-script-authoring'}, 2),
                    ):
                        with self.subTest(code=code):
                            raw_builtin = archive.read_builtin(code)
                            self.assertEqual(raw_builtin, (archive.BUILTIN_PACKAGES_ROOT / f'{code}.zip').read_bytes())
                            package = archive.parse_package(raw_builtin)
                            self.assertNotIn('temperature', package.agent.model_dump())
                            self.assertIsNone(package.agent.model_reference)
                            self.assertEqual(set(package.skills), expected_skills)
                            self.assertEqual(sum(len(ui.tools) for ui in package.ui), tool_count)
                            # The package carries component assets, not deployment identity or credentials.
                            with zipfile.ZipFile(io.BytesIO(raw_builtin)) as bundled_zip:
                                for filename in ('manifest.json', 'agent.json', 'ui-tools.json'):
                                    text = bundled_zip.read(filename).decode('utf-8')
                                    self.assertNotIn('test.deepfos.com', text)
                                    self.assertNotIn('"agent_id":', text)
                                    self.assertNotIn('"api_key":', text)
                            preview = await client.post('/api/v1/agent-packages/preview', data={'builtin_code': code})
                            self.assertEqual(preview.status_code, 200, preview.text)
                            p = preview.json()
                            uploaded = await client.post('/api/v1/agent-packages/preview',
                                files={'file': (f'{code}.zip', raw_builtin, 'application/zip')})
                            self.assertEqual(uploaded.status_code, 200, uploaded.text)
                            self.assertEqual(uploaded.json()['digest'], p['digest'])
                            self.assertEqual(p['references']['model']['matched_id'], (await stores.llm_model.list_all())[0].id)
                            self.assertTrue(all(m['matched_code'] is None for m in p['references']['mcps']))
                            # This fixture has no Python backend: the missing reference is reported and skipped.
                            self.assertTrue(all(r['kind'] == 'builtin_tool' for r in p['resources'] if r['status'] == 'missing'))
                            installed = await client.post('/api/v1/agent-packages/install', data={'builtin_code': code,
                                'options': json.dumps({'digest': p['digest'], 'name': package.agent.name, 'model_id': None})})
                            self.assertEqual(installed.status_code, 201, installed.text)
                            result = installed.json()['agent']
                            self.assertIsNone(result['model_id'])
                            self.assertEqual(result['prompt'], package.agent.prompt)
                            self.assertEqual(result['skills'], package.agent.skills)
                            self.assertTrue(all(not c.startswith('mcp:') for c in result['capabilities']))
                            self.assertIn(package.ui[0].code, result['capabilities'])
                            for skill, (raw, _) in package.skills.items():
                                self.assertEqual(runtime.state(skill).status, RUNTIME_READY)
                                with zipfile.ZipFile(io.BytesIO(raw)) as z:
                                    for name in z.namelist():
                                        relative = Path(name).relative_to(skill)
                                        self.assertNotIn('__pycache__', relative.parts)
                                        self.assertNotIn('tests', relative.parts)
                                        self.assertEqual((runtime.root / skill / relative).read_bytes(), z.read(name))
                            exported = await client.get(f'/api/v1/agents/{result["id"]}/export')
                            self.assertEqual(exported.status_code, 200)
                            parsed_export = archive.parse_package(exported.content)
                            self.assertEqual(parsed_export.agent.prompt, package.agent.prompt)
                            self.assertEqual(parsed_export.ui, package.ui)
                            self.assertEqual(parsed_export.skills, package.skills)
                    self.assertEqual(len(await stores.agent.list_all()), 2)
            finally:
                reset_context()
                await ctx.aclose()
