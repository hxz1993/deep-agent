"""Release-specific lifecycle assertions beyond the generated installation matrix."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import unittest
from unittest.mock import patch

from pydantic_ai import Agent as RuntimeAgent, DeferredToolRequests, DeferredToolResults
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import UsageLimits
from sqlalchemy import select

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.spec import AgentSpec
from deep_agent.agent_packages import archive
from deep_agent.agent_packages.archive import InstallOptions, PackageConflict
from deep_agent.agent_packages.service import export_package, install_package, preview_package
from deep_agent.capabilities.ui.registry import prepare_ui_tool_packages_for_spec
from deep_agent.db.models import Agent, LlmProvider, SkillPackage, UiToolDefinition, UiToolPackage
from deep_agent.schemas.llm_model import LlmModelUpdate
from deep_agent.schemas.ui_tools import UiToolCreate, UiToolPackageCreate
from tests.unit import test_agent_packages as fixtures
from tests.unit import test_configuration_import as imports
from tests.unit.test_skill_packages import skill_zip


class AgentPackageLifecycleRegressions(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixtures.AgentPackageServiceTests.asyncSetUp
    asyncTearDown = fixtures.AgentPackageServiceTests.asyncTearDown
    options = fixtures.AgentPackageServiceTests.options
    rows = fixtures.AgentPackageServiceTests.rows
    active_files = fixtures.AgentPackageServiceTests.active_files

    def package(self, *, upgraded=False, second=False):
        files = fixtures.package_files(resources=True, agent_changes={'prompt': 'Upgraded prompt' if upgraded else 'Initial prompt'})
        ui = json.loads(files['ui-tools.json'])
        ui[0]['tools'][0]['description'] = 'Upgraded choice' if upgraded else 'Initial choice'
        if second:
            ui[0]['tools'].append({**ui[0]['tools'][0], 'name':'package_fixture_extra', 'description':'Retained shared extra'})
        if upgraded:
            files['skills/example-skill.zip'] = skill_zip(scripts=False)
            agent = json.loads(files['agent.json'])
            agent['ui_tool_selection'] = {'ui:package-fixture':['package_fixture_choice']}
            files['agent.json'] = archive.canonical(agent)
        files['ui-tools.json'] = archive.canonical(ui)
        return archive.parse_package(archive.build_zip(files))

    async def overwrite_options(self, package, agent, **kwargs):
        preview = await preview_package(self.ctx, package, mode='overwrite', target_agent_id=agent.id)
        return InstallOptions(digest=preview['digest'], mode='overwrite', target_agent_id=agent.id, name=agent.name, model_id=None, **kwargs)

    async def test_install_upgrade_reexport_reinstall_uses_whole_package_and_updated_skill(self):
        initial = self.package(second=True)
        first = (await install_package(self.ctx, initial, await self.options(initial, name='Lifecycle original'), {}))['agent']
        updated = self.package(upgraded=True)
        installed = (await install_package(self.ctx, updated, await self.overwrite_options(updated, first), {}))['agent']
        self.assertEqual(installed.id, first.id)
        self.assertEqual(installed.prompt, 'Upgraded prompt')
        exported = archive.parse_package(await export_package(self.ctx, first.id, {}))
        self.assertEqual(exported.agent.prompt, 'Upgraded prompt')
        self.assertNotIn('ui_tool_selection', exported.agent.model_dump())
        tools = {tool.name:tool.description for tool in exported.ui[0].tools}
        self.assertEqual(tools, {'package_fixture_choice':'Upgraded choice', 'package_fixture_extra':'Retained shared extra'})
        self.assertEqual(exported.skills['example-skill'][0], updated.skills['example-skill'][0])
        clone = (await install_package(self.ctx, exported, await self.options(exported, name='Lifecycle reinstalled'), {}))['agent']
        self.assertNotEqual(clone.id, first.id)
        self.assertEqual(next(row for row in await self.rows(Agent) if row.id == clone.id).ui_tool_selection, {})
        spec = AgentSpec(name='check',model='test',instructions='',capabilities=clone.capabilities)
        packages, hints = await prepare_ui_tool_packages_for_spec(self.stores,spec)
        self.assertEqual(set(hints), {'package_fixture_choice', 'package_fixture_extra'})
        self.assertEqual(len(await self.rows(UiToolPackage)),1)
        self.assertEqual(len(await self.rows(UiToolDefinition)),2)
        self.assertEqual(len(await self.rows(SkillPackage)),1)

    async def test_overwrite_preserves_historical_session_and_pending_ui_can_resume(self):
        initial = self.package(second=True)
        original = (await install_package(self.ctx,initial,await self.options(initial,name='Pending UI owner'),{}))['agent']
        coordinator=self.stores.coordinator
        persister=EventPersister(coordinator)
        await persister.start()
        async def runner_for(row):
            spec=AgentSpec(name=row.name,model='test',instructions=row.prompt,capabilities=row.capabilities)
            packages,hints=await prepare_ui_tool_packages_for_spec(self.stores,spec)
            model=TestModel(call_tools=['package_fixture_choice'],custom_output_text='Confirmed after upgrade')
            agent=RuntimeAgent(model,deps_type=AgentDeps,toolsets=[p.toolset for p in packages],output_type=[str,DeferredToolRequests])
            return AgentRunner(agent,coordinator,UsageLimits(request_limit=10),ui_tool_hints=hints)
        try:
            runner=await runner_for(original)
            deps=AgentDeps(session_id='upgrade-pending-ui',user='lifecycle-user',agent_id=original.id)
            first=[e async for e in runner.run_stream('upgrade-pending-ui','Original request',deps,persister=persister)]
            await persister.flush('upgrade-pending-ui',agent_id=original.id)
            self.assertEqual(first[-1]['status'],'pending')
            call_id=first[-1]['deferred']['calls'][0]['tool_call_id']
            before=ModelMessagesTypeAdapter.dump_json(await coordinator.get_history('upgrade-pending-ui',agent_id=original.id))
            updated=self.package(upgraded=True)
            row=(await install_package(self.ctx,updated,await self.overwrite_options(updated,original),{}))['agent']
            self.assertEqual(row.id,original.id)
            after=ModelMessagesTypeAdapter.dump_json(await coordinator.get_history('upgrade-pending-ui',agent_id=original.id))
            self.assertEqual(after,before)
            resumed=await runner_for(row)
            final=[e async for e in resumed.run_stream('upgrade-pending-ui','',deps,
                deferred_tool_results=DeferredToolResults(calls={call_id:'Accepted choice'}),persister=persister)]
            self.assertEqual(final[-1]['status'],'completed')
            self.assertEqual(final[-1]['output'],'Confirmed after upgrade')
            self.assertTrue(any(e.get('type')=='tool_result' for e in final))
        finally:
            await persister.aclose()

    async def test_target_deleted_after_preview_is_not_recreated(self):
        package=fixtures.make_package()
        target=(await install_package(self.ctx,package,await self.options(package),{}))['agent']
        options=await self.overwrite_options(package,target)
        self.assertTrue(await self.stores.agent.delete(target.id))
        with self.assertRaises(PackageConflict):
            await install_package(self.ctx,package,options,{})
        self.assertEqual(await self.rows(Agent),[])

    async def test_model_disabled_after_preview_rejects_install_without_resource_writes(self):
        package=self.package()
        model=(await self.stores.llm_model.list_all())[0]
        options=await self.options(package,model_id=model.id)
        await self.stores.llm_configuration.update_model(model.id,LlmModelUpdate(enabled=False))
        with self.assertRaises(PackageConflict):
            await install_package(self.ctx,package,options,{})
        self.assertEqual(await self.rows(Agent),[])
        self.assertEqual(await self.rows(SkillPackage),[])
        self.assertEqual(self.active_files(),0)

    async def test_existing_provider_with_all_models_disabled_installs_without_model(self):
        self.assertGreater(len(await self.rows(LlmProvider)), 0)
        models = await self.stores.llm_model.list_all()
        self.assertGreater(len(models), 0)
        for model in models:
            await self.stores.llm_configuration.update_model(model.id, LlmModelUpdate(enabled=False))
        package = fixtures.make_package()
        preview = await preview_package(self.ctx, package)
        self.assertEqual(preview['models'], [])
        self.assertIsNone(preview['references']['model']['matched_id'])
        # Omit model_id to exercise the default selection, rather than forcing null.
        options = InstallOptions(digest=preview['digest'], name='Provider without available models')
        installed = (await install_package(self.ctx, package, options, {}))['agent']
        self.assertIsNone(installed.model_id)
        self.assertIsNone((await self.rows(Agent))[0].model_id)
        self.assertGreater(len(await self.rows(LlmProvider)), 0)

    async def test_concurrent_overwrites_have_one_winner_without_mixed_state(self):
        original=fixtures.make_package()
        target=(await install_package(self.ctx,original,await self.options(original),{}))['agent']
        packages=[fixtures.make_package(agent_changes={'prompt':prompt}) for prompt in ('Concurrent first','Concurrent second')]
        options=[await self.overwrite_options(package,target) for package in packages]
        outcomes=await asyncio.gather(*(install_package(self.ctx,package,option,{}) for package,option in zip(packages,options)),return_exceptions=True)
        self.assertEqual(sum(isinstance(outcome,PackageConflict) for outcome in outcomes),1)
        successes=[outcome for outcome in outcomes if isinstance(outcome,dict)]
        self.assertEqual(len(successes),1)
        rows=await self.rows(Agent)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0].id,target.id)
        self.assertIn(rows[0].prompt,('Concurrent first','Concurrent second'))
        self.assertEqual(rows[0].prompt,successes[0]['agent'].prompt)

    async def test_resource_deleted_while_install_uploads_forces_repreview_and_cleans_new_file(self):
        package=self.package()
        ui=package.ui[0]
        await self.stores.ui_tool_package.create_manual(UiToolPackageCreate(code=ui.code,label=ui.label,description=ui.description))
        await self.stores.ui_tool.create_manual(UiToolCreate.model_validate(ui.tools[0].model_dump()))
        options=await self.options(package)
        uploaded,release=asyncio.Event(),asyncio.Event()
        upload=self.files.upload
        async def paused_upload(**kwargs):
            result=await upload(**kwargs)
            uploaded.set()
            await release.wait()
            return result
        task=None
        try:
            with patch.object(self.files,'upload',side_effect=paused_upload):
                task=asyncio.create_task(install_package(self.ctx,package,options,{}))
                await asyncio.wait_for(uploaded.wait(),3)
                self.assertTrue(await self.stores.ui_tool.delete('package_fixture_choice'))
                release.set()
                with self.assertRaises(PackageConflict):
                    await asyncio.wait_for(task,3)
            self.assertEqual(await self.rows(Agent),[])
            self.assertEqual(await self.rows(SkillPackage),[])
            self.assertEqual(await self.rows(UiToolDefinition),[])
            self.assertEqual(self.active_files(),0)
        finally:
            release.set()
            if task: await asyncio.gather(task,return_exceptions=True)


class AgentScopeConfigurationRoundtrip(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=imports.ConfigurationImportTests.asyncSetUp
    asyncTearDown=imports.ConfigurationImportTests.asyncTearDown
    _url=staticmethod(imports.ConfigurationImportTests._url)
    _backup=imports.ConfigurationImportTests._backup
    _target_engine=imports.ConfigurationImportTests._target_engine

    async def test_actual_backup_export_and_import_keep_partial_and_empty_scope(self):
        from maintenance.config_backup import export_backup
        await self._backup(revision='010')
        source=self.root/'source-False-010.db'
        self.assertTrue(source.is_file())
        scope={'ui:builtin':['builtin__confirm']}
        with sqlite3.connect(source) as db:
            db.execute('UPDATE agents SET capabilities=?,ui_tool_selection=?,model_id=NULL',(json.dumps(['ui:builtin']),json.dumps(scope)))
            db.row_factory = sqlite3.Row
            empty = dict(db.execute('SELECT * FROM agents').fetchone())
            empty.update(id='empty-scope-agent', name='Empty scope', ui_tool_selection=json.dumps({'ui:builtin': []}))
            db.execute(f"INSERT INTO agents ({','.join(empty)}) VALUES ({','.join('?' for _ in empty)})", tuple(empty.values()))
        backup=await export_backup(self._url(source),environment='test',output_root=self.root/'scoped-backup',git_commit='test')
        engine=await self._target_engine()
        try:
            plan=await imports.build_configuration_import_plan(backup.path,target_engine=engine)
            await imports.apply_configuration_import(plan,target_engine=engine,file_store=imports._MemoryFileStore(),request_headers={})
            async with engine.connect() as db:
                rows=(await db.execute(select(Agent.id, Agent.ui_tool_selection,Agent.model_id))).all()
                self.assertEqual({row.id: row.ui_tool_selection for row in rows}, {
                    'agent-1': {'ui:builtin': ['builtin__confirm']},
                    'empty-scope-agent': {'ui:builtin': []},
                })
                self.assertTrue(all(row.model_id is None for row in rows))
        finally: await engine.dispose()
