"""Agent package format, create/overwrite, rollback and isolated resource behavior."""
from __future__ import annotations

import asyncio
import hashlib
import io
import json
import sqlite3
import stat
import subprocess
import sys
import os
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from deepfos_platform.client.platform import FILE_TYPE_SKILL_PACKAGE

from deep_agent.agent_packages import archive
from deep_agent.agent_packages.archive import InstallOptions, PackageConflict, PackageError
from deep_agent.agent_packages.service import export_package, install_package as install_package_result, preview_package
from deep_agent.db.models import Agent, LlmModel, LlmProvider, McpServer, SkillPackage, UiToolDefinition, UiToolPackage
from deep_agent.files.storage import LocalFileStore
from deep_agent.skills.runtime import RUNTIME_READY, SkillRuntimeLibrary
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.unit.test_skill_packages import skill_zip


async def install_package(*args, **kwargs):
    return (await install_package_result(*args, **kwargs))['agent']


def package_files(*, resources=False, agent_changes=None):
    agent = {'name': '安装包测试助手', 'prompt': '你好 | 精确保留\\n参数 = 内容',
             'capabilities': [], 'skills': []}
    ui = []
    files = {}
    if resources:
        agent['capabilities'] = ['ui:package-fixture', 'mcp:optional-service']
        agent['skills'] = ['example-skill']
        files['skills/example-skill.zip'] = skill_zip(scripts=True)
        ui = [{'code': 'ui:package-fixture', 'label': '验收交互', 'description': 'UI fixture',
               'tools': [{'name': 'package_fixture_choice', 'package_code': 'ui:package-fixture',
                          'description': '选择一个结果', 'widget': 'fixture-choice',
                          'parameters_json_schema': {'type': 'object', 'properties': {'choice': {'type': 'string'}}, 'required': ['choice']},
                          'result_schema': {'type': 'string'}, 'metadata': {'hint': '原样保留'}}]}]
    agent.update(agent_changes or {})
    files.update({'manifest.json': archive.canonical({'format': 'deepagent-agent-package', 'schema_version': 1, 'exported_from': '0.9.11'}),
                  'agent.json': archive.canonical(agent), 'ui-tools.json': archive.canonical(ui)})
    return files


def make_package(**kwargs):
    return archive.parse_package(archive.build_zip(package_files(**kwargs)))


class AgentPackageArchiveTests(unittest.TestCase):
    def test_prompt_version_only_reads_explicit_title_version(self):
        self.assertEqual(archive.prompt_version('# 电子表格智能助手 · deeptable-unified-agent-v85\n正文'), 'V85')
        self.assertEqual(archive.prompt_version('# DeepCube 助手系统提示词 V34\n正文'), 'V34')
        for prompt in ('', '# 无版本\n正文 V85', '正文 v85', '# 助手 v85 的用法'):
            self.assertIsNone(archive.prompt_version(prompt))

    def test_install_options_reject_empty_model_and_invalid_resource_actions(self):
        for fields in ({'model_id': ''}, {'model_id': '   '}, {'name': '  '},
                       {'resources': {'skill:example': 'overwrite'}}, {'future_behavior': True}):
            values = {'digest': 'a' * 64, 'name': 'valid'}
            values.update(fields)
            with self.subTest(fields=fields), self.assertRaises(ValueError):
                InstallOptions.model_validate(values)
        self.assertIsNone(InstallOptions(digest='a' * 64, name='valid', model_id=None).model_id)

    def test_temperature_is_not_part_of_package_format(self):
        self.assertNotIn('temperature', make_package().agent.model_dump())
        self.assertNotIn('temperature', make_package(agent_changes={'temperature': 0.5}).agent.model_dump())

    def test_package_content_and_nested_skill_bytes_are_preserved(self):
        files = package_files(resources=True)
        package = archive.parse_package(archive.build_zip(files))
        self.assertEqual(package.skills['example-skill'][0], files['skills/example-skill.zip'])
        self.assertTrue(package.skills['example-skill'][1]['contains_scripts'])
        self.assertEqual(package.ui[0].tools[0].metadata, {'hint': '原样保留'})
        self.assertEqual(package.agent.prompt, '你好 | 精确保留\\n参数 = 内容')

    def test_software_version_is_reference_but_schema_version_is_strict(self):
        files = package_files()
        for software in ('0.1.0', '99.42.7'):
            files['manifest.json'] = archive.canonical({'format': 'deepagent-agent-package', 'schema_version': 1, 'exported_from': software})
            self.assertEqual(archive.parse_package(archive.build_zip(files)).manifest.exported_from, software)
        for version in (2, True, '1', 1.0):
            files['manifest.json'] = archive.canonical({'format': 'deepagent-agent-package', 'schema_version': version, 'exported_from': '1'})
            with self.subTest(version=version), self.assertRaises(ValueError):
                archive.parse_package(archive.build_zip(files))

    def test_missing_and_duplicate_json_fields_are_rejected(self):
        for changes in ({'agent.json': b'{"name":"a","name":"b"}'},
                        {'agent.json': archive.canonical({'name': 'a', 'future_behavior': True})}):
            files = package_files()
            files.update(changes)
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                archive.parse_package(archive.build_zip(files))
        files = package_files()
        del files['ui-tools.json']
        with self.assertRaises(PackageError):
            archive.parse_package(archive.build_zip(files))

    def test_safe_unknown_package_extensions_are_ignored_but_hashed(self):
        files = package_files(resources=True)
        original = archive.parse_package(archive.build_zip(files))
        files['extensions/future.json'] = b'{"future":true}'
        agent = json.loads(files['agent.json'])
        agent['future_behavior'] = True
        files['agent.json'] = archive.canonical(agent)
        ui = json.loads(files['ui-tools.json'])
        ui[0]['future'] = True
        ui[0]['tools'][0]['future'] = True
        files['ui-tools.json'] = archive.canonical(ui)
        extended = archive.parse_package(archive.build_zip(files))
        self.assertNotEqual(extended.digest, original.digest)
        self.assertEqual(extended.agent.model_dump(), original.agent.model_dump())
        self.assertEqual(extended.ui[0].tools[0].model_dump(), original.ui[0].tools[0].model_dump())

    def test_unsafe_paths_duplicates_links_and_corrupt_zip_are_rejected(self):
        for filename in ('../outside', '/outside', 'skills/../../outside', 'C:/outside', 'skills\\outside.zip', './agent.json'):
            files = package_files()
            files[filename] = b'unsafe'
            with self.subTest(filename=filename), self.assertRaises(PackageError):
                archive.parse_package(archive.build_zip(files))
        for link in (False, True):
            stream = io.BytesIO()
            with zipfile.ZipFile(stream, 'w') as z, warnings.catch_warnings():
                warnings.simplefilter('ignore', UserWarning)
                for name, raw in package_files().items():
                    z.writestr(name, raw)
                info = zipfile.ZipInfo('agent.json' if not link else 'skills/link.zip')
                if link:
                    info.external_attr = (stat.S_IFLNK | 0o777) << 16
                z.writestr(info, '../outside')
            with self.assertRaises(PackageError):
                archive.parse_package(stream.getvalue())
        with self.assertRaises(PackageError):
            archive.parse_package(b'not a zip file')

    def test_compressed_expanded_and_nested_file_budgets_are_enforced(self):
        raw = archive.build_zip(package_files(resources=True))
        for key, limit in (('MAX_PACKAGE_BYTES', len(raw) - 1), ('MAX_EXPANDED_BYTES', 50), ('MAX_PACKAGE_FILES', 5)):
            with self.subTest(key=key), patch.object(archive, key, limit), self.assertRaises(PackageError):
                archive.parse_package(raw)

    def test_unrelated_skill_and_ui_resources_are_rejected(self):
        files = package_files(resources=True)
        files['agent.json'] = package_files()['agent.json']
        with self.assertRaises(PackageError):
            archive.parse_package(archive.build_zip(files))
        files = package_files(resources=True)
        files['skills/wrong-code.zip'] = files.pop('skills/example-skill.zip')
        with self.assertRaises(PackageError):
            archive.parse_package(archive.build_zip(files))

    def test_builtin_zip_matches_upload_bytes_and_digest(self):
        with tempfile.TemporaryDirectory(prefix='agent-package-TMP_to_delete-') as temp:
            root = Path(temp)
            raw = archive.build_zip(package_files(resources=True))
            (root / 'example-agent.zip').write_bytes(raw)
            builtin = archive.read_builtin('example-agent', root)
            self.assertEqual(builtin, raw)
            self.assertEqual(archive.parse_package(builtin).digest, archive.parse_package(raw).digest)

    def test_builtin_zip_rejects_traversal_links_missing_and_oversized_files(self):
        with tempfile.TemporaryDirectory(prefix='agent-package-TMP_to_delete-') as temp:
            root = Path(temp)
            raw = archive.build_zip(package_files())
            (root / 'example-agent.zip').write_bytes(raw)
            for code in ('../example-agent', 'EXAMPLE', 'not-found', 'example-agent.zip'):
                with self.subTest(code=code), self.assertRaises(PackageError):
                    archive.read_builtin(code, root)
            (root / 'linked-agent.zip').symlink_to(root / 'example-agent.zip')
            with self.assertRaises(PackageError):
                archive.read_builtin('linked-agent', root)
            (root / 'directory-agent.zip').mkdir()
            with self.assertRaises(PackageError):
                archive.read_builtin('directory-agent', root)
            with patch.object(archive, 'MAX_PACKAGE_BYTES', len(raw) - 1), self.assertRaises(PackageError):
                archive.read_builtin('example-agent', root)
            (root / 'corrupt-agent.zip').write_bytes(b'broken zip')
            with self.assertRaises(PackageError):
                archive.parse_package(archive.read_builtin('corrupt-agent', root))

    def test_default_builtin_location_is_feature_resource_independent_of_cwd(self):
        repo = Path(__file__).resolve().parents[2]
        expected = repo / 'src' / 'deep_agent' / 'agent_packages' / 'builtin'
        with tempfile.TemporaryDirectory(prefix='agent-package-cwd-TMP_to_delete-') as temp:
            code = ('import json; from deep_agent.agent_packages.archive import BUILTIN_PACKAGES_ROOT, read_builtin, parse_package; '
                    'print(json.dumps({"root": str(BUILTIN_PACKAGES_ROOT), '
                    '"name": parse_package(read_builtin("deeptable-assistant")).agent.name}))')
            result = subprocess.run([sys.executable, '-c', code], cwd=temp,
                env={**os.environ, 'PYTHONPATH': str(repo / 'src')}, capture_output=True, text=True, timeout=30, check=True)
            actual = json.loads(result.stdout)
            self.assertEqual(Path(actual['root']), expected)
            self.assertEqual(actual['name'], '电子表格智能助手')


class AgentPackageServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='agent-package-TMP_to_delete-')
        self.root = Path(self.temp.name)
        self.stores, self.engine, self.factory = await make_sqlite_stores(f'sqlite+aiosqlite:///{self.root / "config.db"}')
        self.files = LocalFileStore(self.root / 'files')
        await self.files.initialize()
        self.runtime = SkillRuntimeLibrary(self.root / 'runtime', file_store=self.files)
        self.ctx = SimpleNamespace(stores=self.stores, file_store=self.files, skill_runtime=self.runtime,
                                   registry=SimpleNamespace(codes=lambda: []))

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.temp.cleanup()

    async def options(self, package, *, name='已安装助手', **kwargs):
        preview = await preview_package(self.ctx, package)
        return InstallOptions(digest=preview['digest'], name=name, **{'model_id': None, **kwargs})

    async def rows(self, cls):
        async with self.stores.agent._read() as db:
            return list((await db.execute(select(cls))).scalars())

    def active_files(self):
        with sqlite3.connect(self.files.metadata_path) as db:
            return db.execute('SELECT count(*) FROM files WHERE deleted_at IS NULL').fetchone()[0]

    async def assert_empty_installation(self):
        for cls in (Agent, SkillPackage, UiToolPackage, UiToolDefinition):
            self.assertEqual(await self.rows(cls), [], cls)
        self.assertEqual(self.active_files(), 0)
        self.assertEqual(list(self.files.blob_root.rglob('*.blob')), [])
        self.assertFalse((self.runtime.root / 'example-skill').exists())

    async def test_preview_is_read_only_and_missing_optional_refs_do_not_block_install(self):
        package = make_package(resources=True, agent_changes={'model_reference': {'name': 'not-installed', 'provider_type': 'openai'}})
        preview = await preview_package(self.ctx, package)
        self.assertEqual(preview['references']['model']['matched_id'], (await self.rows(LlmModel))[0].id)
        self.assertEqual(preview['references']['mcps'], [{'code': 'mcp:optional-service', 'matched_code': None}])
        await self.assert_empty_installation()
        installed = await install_package(self.ctx, package, InstallOptions(digest=preview['digest'], name='安装成功', model_id=None), {})
        self.assertIsNone(installed.model_id)
        self.assertIsNone(installed.model)
        self.assertEqual(installed.skills, ['example-skill'])
        self.assertEqual(installed.capabilities, ['ui:package-fixture'])
        self.assertEqual(self.runtime.state('example-skill').status, RUNTIME_READY)
        self.assertTrue((self.runtime.root / 'example-skill/scripts/run.py').is_file())
        self.assertEqual(len(await self.rows(UiToolDefinition)), 1)

    async def test_empty_ui_package_exports_and_reinstalls_without_losing_metadata(self):
        files = package_files(resources=True)
        ui = json.loads(files['ui-tools.json'])
        ui[0]['tools'] = []
        files['ui-tools.json'] = archive.canonical(ui)
        package = archive.parse_package(archive.build_zip(files))
        first = await install_package(self.ctx, package, await self.options(package), {})
        self.assertEqual(first.capabilities, ['ui:package-fixture'])
        self.assertEqual(await self.rows(UiToolDefinition), [])
        exported = archive.parse_package(await export_package(self.ctx, first.id, {}))
        self.assertEqual(exported.ui[0].tools, [])
        self.assertEqual(exported.ui[0].label, '验收交互')
        preview = await preview_package(self.ctx, exported)
        self.assertEqual(next(r['status'] for r in preview['resources'] if r['kind'] == 'ui'), 'reuse')
        second = await install_package(self.ctx, exported, InstallOptions(digest=preview['digest'], name='empty tools reinstall'), {})
        self.assertEqual(second.capabilities, first.capabilities)
        self.assertEqual(len(await self.rows(UiToolPackage)), 1)
        ui[0]['label'] = 'different incoming label'
        files['ui-tools.json'] = archive.canonical(ui)
        changed = archive.parse_package(archive.build_zip(files))
        preview = await preview_package(self.ctx, changed)
        resource = next(r for r in preview['resources'] if r['kind'] == 'ui')
        self.assertEqual(resource['status'], 'update')
        third = await install_package(self.ctx, changed, InstallOptions(digest=preview['digest'], name='update empty package'), {})
        self.assertEqual(third.capabilities, first.capabilities)
        self.assertEqual((await self.rows(UiToolPackage))[0].label, 'different incoming label')

    async def test_export_install_roundtrip_preserves_full_resources_without_credentials_or_ids(self):
        package = make_package(resources=True)
        model = (await self.rows(LlmModel))[0]
        installed = await install_package(self.ctx, package, await self.options(package, model_id=model.id), {})
        raw = await export_package(self.ctx, installed.id, {})
        exported = archive.parse_package(raw)
        self.assertEqual(exported.agent.prompt, package.agent.prompt)
        self.assertEqual(exported.skills['example-skill'][0], package.skills['example-skill'][0])
        self.assertEqual(exported.ui[0].model_dump(), package.ui[0].model_dump())
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            text = '\n'.join(z.read(n).decode() for n in ('manifest.json', 'agent.json', 'ui-tools.json'))
        for private in (installed.id, model.id, 'test-key', 'test.invalid', (await self.rows(SkillPackage))[0].platform_file_id):
            self.assertNotIn(private, text)
        preview = await preview_package(self.ctx, exported)
        self.assertEqual({r['status'] for r in preview['resources']}, {'reuse'})
        self.assertEqual(preview['references']['model']['matched_id'], model.id)
        second = await install_package(self.ctx, exported, InstallOptions(digest=preview['digest'], name='再安装'), {})
        self.assertNotEqual(second.id, installed.id)
        self.assertEqual(self.active_files(), 1)
        self.assertEqual(len(await self.rows(SkillPackage)), 1)
        self.assertEqual(len(await self.rows(UiToolDefinition)), 1)

    async def test_same_code_resources_update_atomically_and_shared_agent_keeps_associations(self):
        package = make_package(resources=True)
        original = await install_package(self.ctx, package, await self.options(package), {})
        changed = package_files(resources=True)
        changed['skills/example-skill.zip'] = skill_zip(scripts=False)
        ui = json.loads(changed['ui-tools.json'])
        ui[0]['label'] = 'Updated package label'
        changed['ui-tools.json'] = archive.canonical(ui)
        incoming = archive.parse_package(archive.build_zip(changed))
        result = await install_package_result(self.ctx, incoming, await self.options(incoming,name='updated'), {})
        installed = result['agent']
        self.assertEqual(installed.skills, ['example-skill'])
        self.assertEqual(installed.capabilities, ['ui:package-fixture'])
        self.assertEqual((await self.rows(UiToolPackage))[0].label, 'Updated package label')
        self.assertFalse((self.runtime.root / 'example-skill/scripts/run.py').exists())
        self.assertEqual((await self.stores.agent.get_by_id(original.id)).skills, original.skills)
        self.assertEqual({r['status'] for r in result['results'] if r['kind'] in ('skill','ui')}, {'update'})

    async def test_missing_builtin_resources_skip_automatically_and_are_reported(self):
        package = make_package(agent_changes={'skills': ['unknown-skill'], 'capabilities': ['builtin:unknown']})
        result = await install_package_result(self.ctx, package, await self.options(package), {})
        self.assertEqual(result['agent'].skills, [])
        self.assertEqual(result['agent'].capabilities, [])
        self.assertEqual({r['code'] for r in result['missing']}, {'unknown-skill','builtin:unknown'})

    async def test_resource_changes_during_download_do_not_overwrite_newer_runtime(self):
        package = make_package(resources=True)
        await install_package(self.ctx, package, await self.options(package), {})
        options = await self.options(package, name='stale installer')
        fresh_payload = skill_zip(scripts=False)
        original_download = self.runtime.download_package
        original_materialize = self.runtime.materialize
        async def replace_resource_during_download(row, **kwargs):
            old_payload = await original_download(row, **kwargs)
            stored = await self.files.upload(file_type=FILE_TYPE_SKILL_PACKAGE, file_name='example-skill.zip',
                mime='application/zip', data=fresh_payload, request_headers={'user': 'test-admin'})
            async with self.stores.agent._write() as db:
                current = await db.get(SkillPackage, row.code)
                current.platform_file_id = stored.id
                current.content_hash = hashlib.sha256(fresh_payload).hexdigest()
                current.file_size = len(fresh_payload)
                await db.flush()
            latest = (await self.rows(SkillPackage))[0]
            state = await original_materialize(latest, payload=fresh_payload, force=True)
            self.assertEqual(state.status, RUNTIME_READY)
            return old_payload
        with patch.object(self.runtime, 'download_package', side_effect=replace_resource_during_download), \
             patch.object(self.runtime, 'materialize', wraps=original_materialize) as materialize:
            with self.assertRaises(PackageConflict):
                await install_package(self.ctx, package, options, {})
            materialize.assert_not_called()
        self.assertFalse((self.runtime.root / 'example-skill/scripts/run.py').exists())
        self.assertEqual(self.runtime.state('example-skill').content_hash, (await self.rows(SkillPackage))[0].content_hash)
        self.assertEqual(len(await self.rows(Agent)), 1)

    async def test_target_change_after_preview_requires_new_preview(self):
        package = make_package()
        options = await self.options(package)
        async with self.stores.agent._write() as db:
            model = (await db.execute(select(LlmModel))).scalars().first()
            model.name = 'changed after preview'
        with self.assertRaises(PackageConflict):
            await install_package(self.ctx, package, options, {})
        await self.assert_empty_installation()

    async def test_duplicate_name_leaves_no_new_resources_or_files(self):
        simple = make_package()
        await install_package(self.ctx, simple, await self.options(simple), {})
        rich = make_package(resources=True)
        with self.assertRaises(PackageConflict):
            await install_package(self.ctx, rich, await self.options(rich), {})
        self.assertEqual(len(await self.rows(Agent)), 1)
        self.assertEqual(await self.rows(SkillPackage), [])
        self.assertEqual(self.active_files(), 0)
        self.assertFalse((self.runtime.root / 'example-skill').exists())

    async def test_runtime_failure_rolls_back_all_db_rows_files_and_cache(self):
        package = make_package(resources=True)
        options = await self.options(package)
        original = self.runtime.materialize
        async def fail_after_materializing(*args, **kwargs):
            await original(*args, **kwargs)
            return SimpleNamespace(status='error')
        with patch.object(self.runtime, 'materialize', side_effect=fail_after_materializing):
            with self.assertRaisesRegex(PackageError, '运行资源准备失败'):
                await install_package(self.ctx, package, options, {})
        await self.assert_empty_installation()

    async def test_second_upload_failure_compensates_first_upload(self):
        files = package_files(resources=True)
        agent = json.loads(files['agent.json'])
        agent['skills'].append('second-skill')
        files['agent.json'] = archive.canonical(agent)
        files['skills/second-skill.zip'] = skill_zip(name='second-skill')
        package = archive.parse_package(archive.build_zip(files))
        original = self.files.upload
        calls = 0
        async def fail_second(**kwargs):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise OSError('upload fixture failure')
            return await original(**kwargs)
        with patch.object(self.files, 'upload', side_effect=fail_second):
            with self.assertRaises(PackageError):
                await install_package(self.ctx, package, await self.options(package), {})
        self.assertEqual(calls, 2)
        await self.assert_empty_installation()

    async def test_concurrent_installs_with_shared_new_resources_have_one_winner(self):
        package = make_package(resources=True)
        first = await self.options(package, name='并发一')
        second = first.model_copy(update={'name': '并发二'})
        outcomes = await asyncio.gather(install_package(self.ctx, package, first, {}), install_package(self.ctx, package, second, {}), return_exceptions=True)
        self.assertEqual(sum(not isinstance(r, BaseException) for r in outcomes), 1, outcomes)
        self.assertEqual(sum(isinstance(r, PackageConflict) for r in outcomes), 1, outcomes)
        self.assertEqual(len(await self.rows(Agent)), 1)
        self.assertEqual(len(await self.rows(SkillPackage)), 1)
        self.assertEqual(self.active_files(), 1)
        self.assertEqual(self.runtime.state('example-skill').status, RUNTIME_READY)

    async def test_commit_failure_compensates_materialized_skill_and_uploaded_files(self):
        package = make_package(resources=True)
        options = await self.options(package)
        original = AsyncSession.commit
        calls = 0
        async def fail_first_commit(db):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('commit fixture failure')
            return await original(db)
        with patch.object(AsyncSession, 'commit', fail_first_commit):
            with self.assertRaises(PackageError):
                await install_package(self.ctx, package, options, {})
        await self.assert_empty_installation()

    async def test_commit_acknowledgment_failure_keeps_files_referenced_by_committed_rows(self):
        package = make_package(resources=True)
        options = await self.options(package)
        original = AsyncSession.commit
        calls = 0
        async def fail_first_acknowledgment(db):
            nonlocal calls
            calls += 1
            await original(db)
            if calls == 1:
                raise OSError('commit acknowledgment lost')
        with patch.object(AsyncSession, 'commit', fail_first_acknowledgment):
            result = await install_package(self.ctx, package, options, {})
            self.assertEqual(result.name, options.name)
        self.assertEqual(len(await self.rows(Agent)), 1)
        self.assertEqual(len(await self.rows(SkillPackage)), 1)
        self.assertEqual(self.active_files(), 1)
        row = (await self.rows(SkillPackage))[0]
        self.assertEqual(await self.files.download(row.platform_file_id, {}), package.skills['example-skill'][0])
        self.assertEqual(self.runtime.state('example-skill').status, RUNTIME_READY)

    async def test_ui_tool_name_collision_cannot_move_existing_tool(self):
        package = make_package(resources=True)
        async with self.stores.agent._write() as db:
            db.add(UiToolPackage(code='ui:other-package', label='Existing', source='manual'))
            db.add(UiToolDefinition(name='package_fixture_choice', package_code='ui:other-package',
                description='Keep this definition', parameters_json_schema={'type': 'object'}, source='manual'))
        preview = await preview_package(self.ctx, package)
        ui = next(r for r in preview['resources'] if r['kind'] == 'ui')
        self.assertEqual(ui['status'], 'blocked')
        options = InstallOptions(digest=preview['digest'], name='tool name collision')
        with self.assertRaises(PackageConflict):
            await install_package(self.ctx, package, options, {})
        self.assertEqual(await self.rows(Agent), [])
        tool = (await self.rows(UiToolDefinition))[0]
        self.assertEqual(tool.package_code, 'ui:other-package')
        self.assertEqual(tool.description, 'Keep this definition')

    async def test_exact_model_mcp_matches_are_optional_and_invalid_targets_are_rejected(self):
        model = (await self.rows(LlmModel))[0]
        async with self.stores.agent._write() as db:
            db.add(McpServer(name='optional-service', transport='streamable_http', config={'url': 'http://unused.invalid/mcp', 'headers': {'authorization': 'private-reference-only'}}))
        package = make_package(agent_changes={'model_reference': {'name': model.name, 'provider_type': 'openai_compatible'}, 'capabilities': ['mcp:optional-service']})
        preview = await preview_package(self.ctx, package)
        self.assertEqual(preview['references']['model']['matched_id'], model.id)
        self.assertEqual(preview['references']['mcps'][0]['matched_code'], 'mcp:optional-service')
        self.assertNotIn('private-reference-only', json.dumps(preview))
        with self.assertRaises(PackageConflict):
            await install_package(self.ctx, package, InstallOptions(digest=preview['digest'],name='invalid',model_id='unknown-model'), {})
        installed = await install_package(self.ctx, package, InstallOptions(digest=preview['digest'],name='auto mapped'), {})
        self.assertEqual(installed.model_id, model.id)
        self.assertEqual(installed.capabilities, ['mcp:optional-service'])
        empty = await install_package(self.ctx, package, await self.options(package,name='explicitly cleared'), {})
        self.assertIsNone(empty.model_id)
        self.assertEqual(empty.capabilities, ['mcp:optional-service'])

    async def test_request_cancellation_waits_for_install_and_does_not_remove_committed_files(self):
        package = make_package(resources=True)
        options = await self.options(package)
        entered, proceed = asyncio.Event(), asyncio.Event()
        original = self.files.upload
        async def paused_upload(**kwargs):
            entered.set()
            await proceed.wait()
            return await original(**kwargs)
        with patch.object(self.files, 'upload', side_effect=paused_upload):
            task = asyncio.create_task(install_package(self.ctx, package, options, {}))
            try:
                await asyncio.wait_for(entered.wait(), 5)
                task.cancel()
                await asyncio.sleep(0)
                self.assertFalse(task.done())
                proceed.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
            finally:
                proceed.set()
                if not task.done():
                    await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(len(await self.rows(Agent)), 1)
        self.assertEqual(self.active_files(), 1)
        self.assertEqual(self.runtime.state('example-skill').status, RUNTIME_READY)

    async def test_export_missing_or_tampered_skill_file_fails_instead_of_partial_zip(self):
        package = make_package(resources=True)
        installed = await install_package(self.ctx, package, await self.options(package), {})
        with patch.object(self.files, 'download', return_value=b'wrong-content'):
            with self.assertRaisesRegex(PackageError, '无法完整导出'):
                await export_package(self.ctx, installed.id, {})
        row = (await self.rows(SkillPackage))[0]
        await self.files.delete(row.platform_file_id, {})
        with self.assertRaisesRegex(PackageError, '无法完整导出'):
            await export_package(self.ctx, installed.id, {})

    async def test_overwrite_unchanged_agent_ui_change_commit_failure_restores_previous_resources(self):
        package = make_package(resources=True)
        installed = await install_package(self.ctx, package, await self.options(package), {})
        changed = package_files(resources=True)
        ui = json.loads(changed['ui-tools.json'])
        ui[0]['tools'][0]['description'] = 'Must roll back UI update'
        changed['ui-tools.json'] = archive.canonical(ui)
        changed['skills/example-skill.zip'] = skill_zip(scripts=False)
        incoming = archive.parse_package(archive.build_zip(changed))
        preview = await preview_package(self.ctx,incoming,mode='overwrite',target_agent_id=installed.id)
        options = InstallOptions(digest=preview['digest'],name=installed.name,model_id=None,mode='overwrite',target_agent_id=installed.id)
        original = AsyncSession.commit
        calls = 0
        async def fail_first_commit(db):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise OSError('forced overwrite commit failure')
            return await original(db)
        with patch.object(AsyncSession,'commit',fail_first_commit):
            with self.assertRaises(PackageError):
                await install_package_result(self.ctx,incoming,options,{})
        self.assertEqual((await self.rows(UiToolDefinition))[0].description,package.ui[0].tools[0].description)
        row = (await self.rows(SkillPackage))[0]
        self.assertEqual(await self.files.download(row.platform_file_id,{}),package.skills['example-skill'][0])
        self.assertTrue((self.runtime.root/'example-skill/scripts/run.py').is_file())
        self.assertEqual(self.runtime.state('example-skill').content_hash,row.content_hash)
        self.assertEqual(self.active_files(),1)
        self.assertEqual(len(await self.rows(Agent)),1)
        self.assertEqual((await self.rows(Agent))[0].id,installed.id)

    async def test_overwrite_same_agent_only_ui_change_commit_failure_is_not_misclassified_success(self):
        package = make_package(resources=True)
        installed = await install_package(self.ctx,package,await self.options(package),{})
        changed = package_files(resources=True)
        ui = json.loads(changed['ui-tools.json']);ui[0]['tools'][0]['description']='UI-only change'
        changed['ui-tools.json']=archive.canonical(ui)
        incoming=archive.parse_package(archive.build_zip(changed))
        preview=await preview_package(self.ctx,incoming,mode='overwrite',target_agent_id=installed.id)
        options=InstallOptions(digest=preview['digest'],name=installed.name,model_id=None,mode='overwrite',target_agent_id=installed.id)
        original=AsyncSession.commit;calls=0
        async def fail_first(db):
            nonlocal calls
            calls+=1
            if calls==1:raise OSError('UI-only commit failed')
            return await original(db)
        with patch.object(AsyncSession,'commit',fail_first):
            with self.assertRaises(PackageError):await install_package_result(self.ctx,incoming,options,{})
        self.assertEqual((await self.rows(UiToolDefinition))[0].description,package.ui[0].tools[0].description)
        self.assertEqual(self.active_files(),1)

    async def test_old_ui_selection_is_ignored_during_install_and_export(self):
        files=package_files(resources=True)
        ui=json.loads(files['ui-tools.json'])
        additional={**ui[0]['tools'][0],'name':'package_fixture_second'}
        ui[0]['tools'].append(additional);files['ui-tools.json']=archive.canonical(ui)
        agent=json.loads(files['agent.json']);agent['ui_tool_selection']={'ui:package-fixture':['package_fixture_second']}
        files['agent.json']=archive.canonical(agent)
        package=archive.parse_package(archive.build_zip(files))
        installed=await install_package(self.ctx,package,await self.options(package),{})
        self.assertNotIn('ui_tool_selection', installed.model_dump())
        self.assertEqual((await self.rows(Agent))[0].ui_tool_selection, {})
        exported=archive.parse_package(await export_package(self.ctx,installed.id,{}))
        self.assertNotIn('ui_tool_selection', exported.agent.model_dump())
        self.assertEqual(len(exported.ui[0].tools),2)
        agent['ui_tool_selection']['ui:package-fixture']=['not-in-package']
        files['agent.json']=archive.canonical(agent)
        self.assertNotIn('ui_tool_selection', archive.parse_package(archive.build_zip(files)).agent.model_dump())

    async def test_checkpoint_cleanup_failure_does_not_replace_committed_success(self):
        from deep_agent.agent_packages.runtime_transaction import RuntimeCheckpoint
        package=make_package(resources=True)
        original=RuntimeCheckpoint.discard
        async def fail_after_cleanup(checkpoint):
            await original(checkpoint)
            raise OSError('temporary cleanup failure')
        with patch.object(RuntimeCheckpoint,'discard',fail_after_cleanup):
            result=await install_package_result(self.ctx,package,await self.options(package),{})
        self.assertEqual(result['agent'].name,'已安装助手')
        self.assertEqual(len(await self.rows(Agent)),1)
        self.assertEqual(self.active_files(),1)
        self.assertEqual(self.runtime.state('example-skill').status,RUNTIME_READY)

    async def test_second_skill_runtime_failure_restores_first_shared_skill_and_database(self):
        from tests.helpers.agent_package_matrix import skill_bytes
        files=package_files(agent_changes={'skills':['matrix-skill-a','matrix-skill-b']})
        for code in ('matrix-skill-a','matrix-skill-b'):files[f'skills/{code}.zip']=skill_bytes(code,1)
        original_package=archive.parse_package(archive.build_zip(files))
        original_agent=await install_package(self.ctx,original_package,await self.options(original_package),{})
        original_rows={r.code:(r.platform_file_id,r.content_hash) for r in await self.rows(SkillPackage)}
        for code in original_rows:files[f'skills/{code}.zip']=skill_bytes(code,2)
        incoming=archive.parse_package(archive.build_zip(files))
        materialize=self.runtime.materialize
        async def fail_second(row,**kwargs):
            if row.code=='matrix-skill-b':raise OSError('second runtime write failed')
            return await materialize(row,**kwargs)
        with patch.object(self.runtime,'materialize',side_effect=fail_second):
            with self.assertRaises(PackageError):
                await install_package_result(self.ctx,incoming,await self.options(incoming,name='should roll back'),{})
        self.assertEqual(len(await self.rows(Agent)),1)
        self.assertEqual((await self.rows(Agent))[0].id,original_agent.id)
        self.assertEqual({r.code:(r.platform_file_id,r.content_hash) for r in await self.rows(SkillPackage)},original_rows)
        for row in await self.rows(SkillPackage):
            self.assertEqual(await self.files.download(row.platform_file_id,{}),skill_bytes(row.code,1))
            self.assertEqual(self.runtime.state(row.code).content_hash,row.content_hash)
            self.assertEqual((self.runtime.root/row.code/'scripts/calculate.py').read_text(),'print(1 + 40)\n')
        self.assertEqual(self.active_files(),2)

    async def test_model_same_name_cross_provider_wins_over_alphabetical_fallback(self):
        original=(await self.rows(LlmModel))[0]
        async with self.stores.agent._write() as db:
            row=await db.get(LlmModel,original.id);row.name='A-first-fallback'
            provider=LlmProvider(name='Cross-provider',provider_type='deepseek',use_default_base_url=False,base_url='https://api.deepseek.com',api_key='test-key')
            db.add(provider);await db.flush()
            model=LlmModel(name='Z-reference-match',model='test',provider_id=provider.id,enabled=True,input_modalities=['text'],context_window_tokens=4096,max_output_tokens=256)
            db.add(model);await db.flush();matched=model.id
        package=make_package(agent_changes={'model_reference':{'name':'Z-reference-match','provider_type':'openai'}})
        preview=await preview_package(self.ctx,package)
        self.assertEqual(preview['references']['model']['matched_id'],matched)
        installed=await install_package(self.ctx,package,InstallOptions(digest=preview['digest'],name='cross-provider match'),{})
        self.assertEqual(installed.model_id,matched)

    async def test_multiple_same_name_models_choose_stable_id_order(self):
        existing=(await self.rows(LlmModel))[0]
        async with self.stores.agent._write() as db:
            row=await db.get(LlmModel,existing.id);row.name='Same reference'
            provider=LlmProvider(name='Second matching provider',provider_type='deepseek',use_default_base_url=False,base_url='https://api.deepseek.com',api_key='test-key')
            db.add(provider);await db.flush()
            other=LlmModel(id='00000000-0000-4000-8000-000000000001',name='Same reference',model='test',provider_id=provider.id,enabled=True,input_modalities=['text'],context_window_tokens=4096,max_output_tokens=256)
            db.add(other);await db.flush();other_id=other.id
        package=make_package(agent_changes={'model_reference':{'name':'Same reference','provider_type':'openai_compatible'}})
        preview=await preview_package(self.ctx,package)
        self.assertEqual(preview['references']['model']['matched_id'],min(existing.id,other_id))
        installed=await install_package(self.ctx,package,InstallOptions(digest=preview['digest'],name='stable matched model'),{})
        self.assertEqual(installed.model_id,min(existing.id,other_id))
