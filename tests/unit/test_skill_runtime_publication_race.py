"""Current-row coordination prevents old API/chat work from republishing old ZIPs."""
from __future__ import annotations

import asyncio
import threading
import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from deep_agent.agent_packages import archive
from deep_agent.agent_packages.service import install_package
from deep_agent.api.v1.skills import _materialize_current, _resource_root
from deep_agent.db.models import SkillPackage
from deep_agent.skills.runtime import resolve_skills_for_run
from tests.unit import test_agent_packages as fixtures
from tests.unit.test_skill_packages import skill_zip


class SkillRuntimePublicationRaceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AgentPackageServiceTests.asyncSetUp
    asyncTearDown = fixtures.AgentPackageServiceTests.asyncTearDown
    rows = fixtures.AgentPackageServiceTests.rows
    options = fixtures.AgentPackageServiceTests.options

    async def seed(self):
        original = fixtures.make_package(resources=True)
        await install_package(self.ctx, original, await self.options(original, name='Before'), {})
        files = fixtures.package_files(resources=True)
        files['skills/example-skill.zip'] = skill_zip(scripts=False)
        updated = archive.parse_package(archive.build_zip(files))
        return original, updated

    async def test_chat_reload_resource_and_startup_wait_for_install_then_read_current_row(self):
        _, updated = await self.seed()
        options = await self.options(updated, name='After')
        published, release = asyncio.Event(), asyncio.Event()
        attempted = {name: asyncio.Event() for name in ('chat', 'reload', 'resource', 'startup')}
        write = self.stores.skill_package._write
        materialize = self.runtime.materialize
        paused = False

        @asynccontextmanager
        async def observed_write(*, exclusive=False):
            name = asyncio.current_task().get_name()
            if name in attempted:
                attempted[name].set()
            async with write(exclusive=exclusive) as db:
                yield db

        async def paused_publish(row, **kwargs):
            nonlocal paused
            result = await materialize(row, **kwargs)
            if not paused and row.content_hash == updated.skills[row.code][1]['content_hash']:
                paused = True
                published.set()
                await release.wait()
            return result

        async def resource():
            request = Request({'type':'http','headers':[]})
            async with _resource_root('example-skill', request, self.ctx) as (root, index):
                self.assertFalse((root / 'scripts/run.py').exists())
                self.assertFalse(any(item.get('path') == 'scripts/run.py' for item in index))

        tasks = []
        try:
            with patch.object(self.runtime, 'materialize', side_effect=paused_publish), patch.object(self.stores.skill_package, '_write', observed_write):
                installing = asyncio.create_task(install_package(self.ctx, updated, options, {}))
                tasks.append(installing)
                await asyncio.wait_for(published.wait(), 3)
                operations = {
                    'chat': resolve_skills_for_run(self.stores, ['example-skill'], runtime=self.runtime, request_headers={}),
                    'reload': _materialize_current(self.ctx, 'example-skill', request_headers={}, force=True),
                    'resource': resource(),
                    'startup': self.runtime.reconcile(self.stores),
                }
                readers = [asyncio.create_task(operation, name=name) for name, operation in operations.items()]
                tasks.extend(readers)
                await asyncio.wait_for(asyncio.gather(*(event.wait() for event in attempted.values())), 3)
                self.assertTrue(all(not task.done() for task in readers))
                release.set()
                results = await asyncio.wait_for(asyncio.gather(*tasks), 5)
                self.assertEqual(results[0]['agent'].name, 'After')
                self.assertEqual(results[2][0].content_hash, updated.skills['example-skill'][1]['content_hash'])
                current = (await self.rows(SkillPackage))[0]
                self.assertTrue(self.runtime._ready_from_disk(current))
                self.assertFalse((self.runtime.root / 'example-skill/scripts/run.py').exists())
        finally:
            release.set()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def test_stale_create_replace_preparation_rejects_old_row_without_publishing(self):
        original, updated = await self.seed()
        stale = (await self.rows(SkillPackage))[0]
        await install_package(self.ctx, updated, await self.options(updated, name='After'), {})
        with patch.object(self.runtime, 'materialize', wraps=self.runtime.materialize) as publish:
            with self.assertRaises(HTTPException) as error:
                await _materialize_current(self.ctx, stale.code, expected=stale, payload=original.skills[stale.code][0], request_headers={})
            self.assertEqual(error.exception.status_code, 409)
            publish.assert_not_called()
        self.assertTrue(self.runtime._ready_from_disk((await self.rows(SkillPackage))[0]))

    async def test_cancellation_waits_for_directory_worker_before_releasing_barrier(self):
        original, _ = await self.seed()
        row = (await self.rows(SkillPackage))[0]
        started, release = threading.Event(), threading.Event()
        worker = self.runtime._install_payload
        def blocked_publish(*args):
            started.set()
            release.wait(5)
            return worker(*args)
        task = None
        try:
            with patch.object(self.runtime, '_install_payload', side_effect=blocked_publish):
                task = asyncio.create_task(_materialize_current(self.ctx, row.code, expected=row, payload=original.skills[row.code][0], request_headers={}, force=True))
                await asyncio.to_thread(started.wait, 3)
                self.assertTrue(started.is_set())
                task.cancel()
                # An exclusive writer cannot proceed while cancelled publication
                # is still running in its filesystem worker.
                acquired = asyncio.Event()
                async def writer():
                    async with self.stores.skill_package._write(exclusive=True):
                        acquired.set()
                waiting = asyncio.create_task(writer())
                await asyncio.sleep(0)
                self.assertFalse(acquired.is_set())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                await asyncio.wait_for(waiting, 3)
                self.assertTrue(acquired.is_set())
        finally:
            release.set()
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)
