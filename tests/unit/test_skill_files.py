from __future__ import annotations

import asyncio
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pydantic_ai import ModelRetry, ToolFailed, ToolReturn

from deep_agent.skills.package import inspect_skill_directory
from deep_agent.skills.runtime import ResolvedSkill, SkillRuntimeLibrary, SKILL_RUNTIME_MARKER
from deep_agent.skills.script_tool import build_skill_script_toolset
from deep_agent.sandbox.types import SandboxPublishedFile
from tests.unit.test_sandbox_toolset import FakeFileStore, FakeSandboxBackend, _context


class SkillFilesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='TMP_to_delete-skill-files-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / 'scripts').mkdir()
        (self.root / 'SKILL.md').write_text('---\nname: demo\ndescription: demo\n---\n')
        (self.root / 'scripts/check.py').write_text('pass')
        parsed = inspect_skill_directory(self.root, content_hash='fixture', require_directory_match=False)
        self.item = ResolvedSkill(code='demo', source='upload', package_root=self.root,
                                  library_root=self.root.parent, resource_index=parsed.resource_index)
        self.backend = FakeSandboxBackend()
        self.backend.published_files = (SandboxPublishedFile(
            file_name='report.html', mime='text/html', byte_size=3,
            sha256=hashlib.sha256(b'abc').hexdigest(), data=b'abc'),)
        toolset = build_skill_script_toolset({'demo': self.item}, backend=self.backend, timeout_sec=5)
        self.ctx = _context(attachment_bytes={'source': b'input'}, file_store=FakeFileStore())
        self.tool = (await toolset.get_tools(self.ctx))['execute_skill_script']

    async def test_skill_authorized_inputs_and_published_files_share_python_contract(self):
        returned = await self.tool.call_func({'skill_code': 'demo', 'script_path': 'scripts/check.py',
                                             'files': [{'file_id': 'source'}]}, self.ctx)
        self.assertIsInstance(returned, ToolReturn)
        request = self.backend.requests[0]
        self.assertEqual(request.files[0].file_id, 'source')
        self.assertEqual(request.files[0].decoded(), b'input')
        self.assertEqual(request.script_entry, 'scripts/check.py')
        self.assertEqual(returned.return_value['status'], 'ok')
        refs = returned.return_value['files']
        self.assertEqual(refs[0]['file_id'], 'file-1')
        self.assertEqual(self.ctx.deps.session_file_artifacts['file-1'], refs[0])
        self.assertEqual(returned.metadata, {'generated_files': refs})

    async def test_skill_rejects_unowned_file_and_bundle_overwrite_before_execution(self):
        for file in ({'file_id': 'stranger'}, {'file_id': 'source', 'path': 'skill/scripts/check.py'},
                     {'file_id': 'source', 'path': 'Skill/scripts/check.py'},
                     {'file_id': 'source', 'path': 'Outputs/report.html'}):
            with self.subTest(file=file), self.assertRaises(ModelRetry):
                await self.tool.call_func({'skill_code': 'demo', 'script_path': 'scripts/check.py',
                                           'files': [file]}, self.ctx)
        self.assertEqual(self.backend.requests, [])

    async def test_changed_module_hash_fails_before_execution(self):
        (self.root / 'scripts/check.py').write_text('print("changed")')
        with self.assertRaises(ToolFailed):
            await self.tool.call_func({'skill_code': 'demo', 'script_path': 'scripts/check.py'}, self.ctx)
        self.assertEqual(self.backend.requests, [])

    async def test_changed_skill_definition_fails_before_execution(self):
        (self.root / 'SKILL.md').write_text('---\nname: demo\ndescription: replaced version\n---\n')
        with self.assertRaisesRegex(ToolFailed, 'Skill'):
            await self.tool.call_func({'skill_code': 'demo', 'script_path': 'scripts/check.py'}, self.ctx)
        self.assertEqual(self.backend.requests, [])

    async def test_definition_missing_when_resolved_is_not_learned_at_execution(self):
        (self.root / 'SKILL.md').unlink()
        unresolved = ResolvedSkill(code='demo', source='upload', package_root=self.root,
                                   library_root=self.root.parent, resource_index=self.item.resource_index)
        (self.root / 'SKILL.md').write_text('---\nname: demo\ndescription: late definition\n---\n')
        toolset = build_skill_script_toolset({'demo': unresolved}, backend=self.backend, timeout_sec=5)
        tool = (await toolset.get_tools(self.ctx))['execute_skill_script']
        with self.assertRaisesRegex(ToolFailed, 'Skill'):
            await tool.call_func({'skill_code': 'demo', 'script_path': 'scripts/check.py'}, self.ctx)
        self.assertEqual(self.backend.requests, [])

    async def test_skill_upload_failure_does_not_grant_file_access(self):
        self.ctx.deps.file_store = FakeFileStore(fail_upload_at=1)
        with self.assertRaises(ToolFailed):
            await self.tool.call_func({'skill_code': 'demo', 'script_path': 'scripts/check.py'}, self.ctx)
        self.assertEqual(self.ctx.deps.session_file_artifacts, {})

    async def test_cancel_during_second_upload_rolls_back_first_file(self):
        waiting = asyncio.Event()

        class BlockingStore(FakeFileStore):
            async def upload(store, **kwargs):
                if store.uploaded:
                    waiting.set()
                    await asyncio.Future()
                return await super().upload(**kwargs)

        store = BlockingStore()
        self.ctx.deps.file_store = store
        first = self.backend.published_files[0]
        self.backend.published_files = (first, first.model_copy(update={"file_name": "second.html"}))
        task = asyncio.create_task(self.tool.call_func(
            {"skill_code": "demo", "script_path": "scripts/check.py"}, self.ctx))
        try:
            await asyncio.wait_for(waiting.wait(), timeout=5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        finally:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
        self.assertEqual(store.deleted, ["file-1"])
        self.assertEqual(self.ctx.deps.session_file_artifacts, {})


class SkillDefinitionResolutionTests(unittest.TestCase):
    def test_shared_runtime_replacement_during_resolution_does_not_mix_definition(self):
        with tempfile.TemporaryDirectory(prefix='TMP_to_delete-skill-resolve-') as temporary:
            runtime = SkillRuntimeLibrary(Path(temporary))
            root = runtime.root / 'demo'
            root.mkdir()
            (root / 'SKILL.md').write_text('old definition')
            marker = root / SKILL_RUNTIME_MARKER
            marker.write_text(json.dumps({'code': 'demo', 'content_hash': 'old-hash', 'platform_file_id': 'old-file'}))
            row = SimpleNamespace(code='demo', content_hash='old-hash', platform_file_id='old-file', resource_index=[])
            resolved = runtime._resolved(row)
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.definition_hash, hashlib.sha256(b'old definition').hexdigest())
            ready = runtime._ready_from_disk
            checks = 0

            def replace_after_check(value):
                nonlocal checks
                result = ready(value)
                if checks == 0:
                    marker.write_text(json.dumps({'code': 'demo', 'content_hash': 'new-hash', 'platform_file_id': 'new-file'}))
                    (root / 'SKILL.md').write_text('new definition')
                checks += 1
                return result

            with patch.object(runtime, '_ready_from_disk', side_effect=replace_after_check):
                self.assertIsNone(runtime._resolved(row))
            self.assertGreaterEqual(checks, 2)
            self.assertEqual(resolved.definition_hash, hashlib.sha256(b'old definition').hexdigest())
