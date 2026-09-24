"""Independent fixture/oracle for the complete Agent installation product matrix.

No model or MCP network request is made. Each case has its own real SQLite file,
LocalFileStore and SkillRuntime. Expectations come from fixture construction,
not from the installation preview or production comparison helpers.
"""
from __future__ import annotations

import io
import itertools
import json
import shutil
import tempfile
import time
import zipfile
from collections import Counter
from functools import lru_cache
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deep_agent.agent.spec import AgentSpec
from deep_agent.agent_packages import archive
from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.ui.registry import load_effective_ui_tools
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.db.dialect import DbDialect
from deep_agent.db.models import Agent, Base, IntegrationSettings, LlmModel, LlmProvider, McpServer, SkillPackage, UiToolDefinition, UiToolPackage
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.files.storage import LocalFileStore
from deep_agent.skills.package import parse_skill_zip
from deep_agent.skills.runtime import RUNTIME_READY, SkillRuntimeLibrary
from deepfos_platform.client.platform import FILE_TYPE_SKILL_PACKAGE

DIMENSIONS = {
    'source': ('upload', 'builtin'),
    'mode': ('create', 'overwrite'),
    'skill': ('none', 'new', 'same', 'diff', 'mixed'),
    'ui': ('none', 'new', 'same', 'diff', 'same_extra', 'diff_extra', 'mixed', 'mixed_extra'),
    'builtin': ('none', 'matched', 'missing', 'mixed'),
    'mcp': ('none', 'matched', 'missing', 'mixed'),
    'model': ('match', 'first', 'none', 'clear', 'other'),
    'sharing': ('no', 'yes'),
}
TOTAL_CASES = 25_600
IDS = {key: f'00000000-0000-4000-8000-{number:012d}' for number, key in enumerate(('provider','first','match','other','target','whole','partial'), 1)}


def all_cases():
    for index, values in enumerate(itertools.product(*DIMENSIONS.values()), 1):
        yield {'case_id': f'APM-{index:05d}', **dict(zip(DIMENSIONS, values))}


def zip_bytes(files):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w', compression=zipfile.ZIP_DEFLATED) as z:
        for name, raw in sorted(files.items()):
            entry = zipfile.ZipInfo(name, (2026, 9, 12, 0, 0, 0))
            entry.compress_type = zipfile.ZIP_DEFLATED
            entry.external_attr = 0o100644 << 16
            z.writestr(entry, raw)
    return output.getvalue()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True).encode()


@lru_cache(maxsize=12)
def skill_bytes(code, revision):
    return zip_bytes({
        f'{code}/SKILL.md': f'---\nname: {code}\ndescription: Matrix fixture {revision}.\n---\n\n# Matrix skill\nRead references before execution.\n'.encode(),
        f'{code}/manifest.yaml': f'code: {code}\nlabel: Matrix {code}\nversion: {revision}.0.0\nschema_version: "1.0"\n'.encode(),
        f'{code}/references/guide.md': f'确定性验收资源 revision={revision}\n'.encode(),
        f'{code}/scripts/calculate.py': f'print({revision} + 40)\n'.encode(),
    })


def tool(code, letter, revision=2):
    return {'name': f'{code[3:].replace("-", "_")}_{letter}', 'package_code': code,
            'description': f'Matrix {letter} revision {revision}', 'widget': 'matrix-fixture',
            'parameters_json_schema': {'type': 'object', 'properties': {'value': {'type': 'integer'}}, 'required': ['value']},
            'result_schema': {'type': 'integer'}, 'metadata': {'revision': revision}}


def ui_package(code, tools, revision=2):
    return {'code': code, 'label': f'Matrix {code}', 'description': f'Package revision {revision}', 'tools': tools}


def construct_fixture(case):
    incoming_skills, existing_skills, statuses = {}, {}, {}
    if case['skill'] != 'none':
        states = {'matrix-skill-a': case['skill']}
        if case['skill'] == 'mixed': states = {'matrix-skill-a': 'same', 'matrix-skill-b': 'diff', 'matrix-skill-c': 'new'}
        for code, state in states.items():
            incoming_skills[code] = skill_bytes(code, 2)
            if state != 'new': existing_skills[code] = skill_bytes(code, 2 if state == 'same' else 1)
            statuses[('skill', code)] = {'new': 'new', 'same': 'reuse', 'diff': 'update'}[state]
    incoming_ui, existing_ui, tool_statuses = {}, {}, {}
    if case['ui'] != 'none':
        mode = case['ui']; code = 'ui:matrix-one'
        letters = ('a', 'b', 'd') if mode.startswith('mixed') else ('a', 'b')
        incoming_ui[code] = ui_package(code, [tool(code, letter) for letter in letters])
        if mode != 'new':
            old = [tool(code, 'a', 2 if mode.startswith(('same','mixed')) else 1), tool(code, 'b', 1 if mode.startswith(('diff','mixed')) else 2)]
            if mode.endswith('_extra'): old.append(tool(code, 'c', 1))
            existing_ui[code] = ui_package(code, old, 1 if mode.startswith('diff') else 2)
        statuses[('ui', code)] = 'new' if mode == 'new' else 'reuse' if mode.startswith('same') else 'update'
        if mode.startswith('mixed'):
            code = 'ui:matrix-two'
            incoming_ui[code] = ui_package(code, [tool(code, 'a'), tool(code, 'b')])
            statuses[('ui', code)] = 'new'
        for code, package in incoming_ui.items():
            old = {t['name']: t for t in existing_ui.get(code, {}).get('tools', [])}
            for t in package['tools']:
                tool_statuses[t['name']] = 'new' if t['name'] not in old else 'reuse' if old[t['name']] == t else 'update'
    caps = list(incoming_ui)
    skills = list(incoming_skills)
    expected_missing = set()
    if case['builtin'] in ('matched', 'mixed'):
        caps.append('builtin:general'); skills.append('deepfos-readonly-analysis')
        statuses[('builtin_tool', 'builtin:general')] = 'reuse'
        statuses[('builtin_skill', 'deepfos-readonly-analysis')] = 'reuse'
    if case['builtin'] in ('missing', 'mixed'):
        caps.append('builtin:matrix-unavailable'); skills.append('matrix-unavailable-skill')
        statuses[('builtin_tool', 'builtin:matrix-unavailable')] = 'missing'
        statuses[('builtin_skill', 'matrix-unavailable-skill')] = 'missing'
        expected_missing.update(('builtin:matrix-unavailable', 'matrix-unavailable-skill'))
    existing_mcps = []
    if case['mcp'] in ('matched', 'mixed'):
        caps.append('mcp:matrix-present'); existing_mcps.append('matrix-present')
    if case['mcp'] in ('missing', 'mixed'):
        caps.append('mcp:matrix-absent'); expected_missing.add('mcp:matrix-absent')
    reference = 'M-match' if case['model'] in ('match', 'clear', 'other') else 'not-installed'
    agent = {'name': 'Matrix source', 'prompt': '矩阵通用提示词 | key=value\n引用与资源原样保留。',
             'capabilities': caps, 'skills': skills, 'model_reference': {'name': reference, 'provider_type': 'openai'}}
    files = {'manifest.json': encoded({'format': 'deepagent-agent-package', 'schema_version': 1, 'exported_from': '0.9.11'}),
             'agent.json': encoded(agent), 'ui-tools.json': encoded(list(incoming_ui.values()))}
    files.update({f'skills/{code}.zip': raw for code, raw in incoming_skills.items()})
    expected_caps = [c for c in caps if c not in expected_missing]
    expected_skills = [c for c in skills if c not in expected_missing]
    default_model = None if case['model'] == 'none' else IDS['match'] if reference == 'M-match' else IDS['first']
    expected_model = None if case['model'] in ('none', 'clear') else IDS['other'] if case['model'] == 'other' else default_model
    return SimpleNamespace(raw=zip_bytes(files), agent=agent, incoming_skills=incoming_skills, existing_skills=existing_skills,
        incoming_ui=incoming_ui, existing_ui=existing_ui, existing_mcps=existing_mcps, statuses=statuses,
        tool_statuses=tool_statuses, expected_missing=expected_missing, expected_caps=expected_caps,
        expected_skills=expected_skills, default_model=default_model, expected_model=expected_model)


async def create_schema_template(path):
    engine = create_async_engine(f'sqlite+aiosqlite:///{path}')
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    finally:
        await engine.dispose()


async def seed_skill(ctx, code, raw):
    stored = await ctx.file_store.upload(file_type=FILE_TYPE_SKILL_PACKAGE, file_name=f'{code}.zip', mime='application/zip', data=raw, request_headers={})
    parsed = parse_skill_zip(raw)
    try:
        row = SkillPackage(code=code, label=parsed.label, description=parsed.description, version=parsed.version,
            schema_version=parsed.schema_version, source='upload', platform_file_id=stored.id, file_name=f'{code}.zip',
            file_size=len(raw), content_hash=parsed.content_hash, manifest=parsed.manifest, resource_index=parsed.resource_index,
            contains_scripts=parsed.contains_scripts)
    finally:
        shutil.rmtree(parsed.package_root.parent, ignore_errors=True)
    async with ctx.stores.agent._write() as db: db.add(row)
    state = await ctx.skill_runtime.materialize(row, payload=raw)
    assert state.status == RUNTIME_READY


async def matrix_case(case, worker_root, template, app=None):
    began = time.perf_counter(); checks = 0
    def require(condition, message):
        nonlocal checks
        checks += 1
        if not condition: raise AssertionError(message)
    fixture = construct_fixture(case)
    with tempfile.TemporaryDirectory(prefix=case['case_id']+'-', dir=worker_root) as temp:
        root = Path(temp)
        shutil.copyfile(template, root/'case.db')
        engine = create_async_engine(f'sqlite+aiosqlite:///{root / "case.db"}')
        factory = async_sessionmaker(engine, expire_on_commit=False)
        stores = create_data_stores(DbDialect.SQLITE, factory)
        files = LocalFileStore(root/'files'); await files.initialize()
        runtime = SkillRuntimeLibrary(root/'runtime', file_store=files)
        ctx = AppContext.for_testing(stores=stores, db_engine=engine, db_session_factory=factory, file_store=files, skill_runtime=runtime, registry=build_registry())
        init_context(ctx)
        original_builtin = archive.BUILTIN_PACKAGES_ROOT
        archive.BUILTIN_PACKAGES_ROOT = root/'builtin'; archive.BUILTIN_PACKAGES_ROOT.mkdir()
        (archive.BUILTIN_PACKAGES_ROOT/'matrix.zip').write_bytes(fixture.raw)
        try:
            for code, payload in fixture.existing_skills.items(): await seed_skill(ctx, code, payload)
            async with stores.agent._write() as db:
                if case['model'] != 'none':
                    db.add(LlmProvider(id=IDS['provider'], name='Matrix provider', provider_type='openai_compatible', use_default_base_url=False, base_url='http://never-called.invalid/v1', api_key='matrix-test-only'))
                    for key, name in (('first','A-first'),('match','M-match'),('other','Z-other')):
                        db.add(LlmModel(id=IDS[key], provider_id=IDS['provider'], name=name, model='fixture', input_modalities=['text'], context_window_tokens=4096, max_output_tokens=256, enabled=True))
                for name in fixture.existing_mcps:
                    db.add(McpServer(name=name, transport='streamable_http', config={'url':'http://never-called.invalid/mcp'}))
                for code, package in fixture.existing_ui.items():
                    db.add(UiToolPackage(code=code, label=package['label'], description=package['description'], source='manual'))
                    for t in package['tools']:
                        values=dict(t); values['metadata_']=values.pop('metadata')
                        db.add(UiToolDefinition(**values, source='manual'))
                if case['mode']=='overwrite':
                    db.add(Agent(id=IDS['target'], name='Matrix old target', prompt='old target prompt', model_id=None, capabilities=['builtin:obsolete'], skills=[], ui_tool_selection={}))
                    db.add(IntegrationSettings(code='matrix-binding', settings={'agent_id':IDS['target']}))
                if case['sharing']=='yes':
                    for key in ('whole','partial'):
                        selections={}
                        if key=='partial':
                            selections={code:[p['tools'][-1]['name']] for code,p in fixture.existing_ui.items()}
                        db.add(Agent(id=IDS[key], name=f'Matrix {key}', prompt=f'{key} must remain unchanged', model_id=None,
                            capabilities=list(fixture.existing_ui), skills=list(fixture.existing_skills), ui_tool_selection=selections))
            shared_before={}
            if case['sharing']=='yes':
                for key in ('whole','partial'):
                    response=await stores.agent.get_by_id(IDS[key])
                    shared_before[key]={'name':response.name,'prompt':response.prompt,'capabilities':response.capabilities,'skills':response.skills,'ui_tool_selection':response.ui_tool_selection}
            if app is None:
                app=FastAPI(); app.include_router(api_router)
            headers={'user':'matrix-admin','authorization':'Bearer matrix-test-only'}
            source={'files':{'file':('matrix.zip',fixture.raw,'application/zip')}} if case['source']=='upload' else {'data':{'builtin_code':'matrix'}}
            mode={'mode':case['mode']}
            if case['mode']=='overwrite': mode['target_agent_id']=IDS['target']
            source['data']={**source.get('data',{}),**mode}
            async with AsyncClient(transport=ASGITransport(app=app),base_url='http://matrix',headers=headers) as client:
                preview=await client.post('/api/v1/agent-packages/preview',**source)
                require(preview.status_code==200,f'preview HTTP {preview.status_code}: {preview.text[:500]}')
                p=preview.json()
                require(p['references']['model']['matched_id']==fixture.default_model,f'preview default model mismatch: {p["references"]["model"]}, models={p["models"]}')
                actual_status={(r['kind'],r['code']):r['status'] for r in p['resources']}
                for key,expected in fixture.statuses.items(): require(actual_status.get(key)==expected,f'{key}: expected {expected}, got {actual_status.get(key)}')
                for resource in p['resources']:
                    if resource['kind']=='ui':
                        for t in resource.get('tools',[]):
                            if t['name'] in fixture.tool_statuses: require(t['status']==fixture.tool_statuses[t['name']],f'tool {t["name"]} status')
                options={'digest':p['digest'],'name':'Matrix installed',**mode}
                if case['model']=='clear':options['model_id']=None
                elif case['model']=='other':options['model_id']=IDS['other']
                install_source={**source,'data':{**source['data'],'options':json.dumps(options)}}
                # Mode belongs inside options for install, multipart preview fields are not needed.
                install_source['data'].pop('mode',None); install_source['data'].pop('target_agent_id',None)
                installed=await client.post('/api/v1/agent-packages/install',**install_source)
                require(installed.status_code==201,f'install HTTP {installed.status_code}: {installed.text[:500]}')
                result=installed.json(); agent=result['agent']
                require(result['mode']==case['mode'],'returned mode')
                require(agent['name']=='Matrix installed','installed Agent name')
                expected_actions=[(kind,code,status) for (kind,code),status in fixture.statuses.items()]
                expected_actions.append(('agent',agent['id'],'new' if case['mode']=='create' else 'update'))
                expected_actions.extend(('mcp',code,'missing' if code in fixture.expected_missing else 'reuse') for code in fixture.agent['capabilities'] if code.startswith('mcp:'))
                if case['mode']=='overwrite':expected_actions.append(('capability','builtin:obsolete','unlinked'))
                actual_actions=[(r['kind'],r['code'],r['status']) for r in result['results']]
                require(Counter(actual_actions)==Counter(expected_actions),f'actual installation actions mismatch {actual_actions} != {expected_actions}')
                actual_tool_actions={t['name']:t['status'] for r in result['results'] if r['kind']=='ui' for t in r['tools']}
                require(actual_tool_actions==fixture.tool_statuses,'actual installed per-tool actions')
                require(agent['model_id']==fixture.expected_model,'installed model')
                require(agent['prompt']==fixture.agent['prompt'],'prompt changed')
                require(set(agent['capabilities'])==set(fixture.expected_caps),'capability references')
                require(set(agent['skills'])==set(fixture.expected_skills),'skill references')
                require('ui_tool_selection' not in agent,'installed Agent has no per-tool selection')
                require({r['code'] for r in result['missing']}==fixture.expected_missing,'missing references report')
                require(agent['id']==IDS['target'] if case['mode']=='overwrite' else agent['id']!=IDS['target'],'Agent identity semantics')
                reread=await client.get(f'/api/v1/agents/{agent["id"]}')
                require(reread.status_code==200,'Agent reread')
                require('ui_tool_selection' not in reread.json(),'Agent API omits old selection')
                spec=AgentSpec(name=agent['name'],model='test',instructions=agent['prompt'],capabilities=agent['capabilities'],skills=agent['skills'])
                loaded=await load_effective_ui_tools(stores,spec)
                expected_names={t['name'] for code in fixture.incoming_ui for package in
                    (fixture.incoming_ui[code], fixture.existing_ui.get(code, {'tools': []})) for t in package['tools']}
                require({t.name for t in loaded.definitions}==expected_names,'installed runtime loaded extra/missing UI tools')
                for t in loaded.definitions:
                    expected=next(x for u in fixture.incoming_ui.values() for x in u['tools'] if x['name']==t.name) if any(
                        x['name']==t.name for u in fixture.incoming_ui.values() for x in u['tools']) else next(
                        x for u in fixture.existing_ui.values() for x in u['tools'] if x['name']==t.name)
                    require(t.description==expected['description'],f'UI runtime description {t.name}')
                for code,raw in fixture.incoming_skills.items():
                    row=await stores.skill_package.get_by_code(code)
                    require(await files.download(row.platform_file_id,{})==raw,'Skill blob not replaced/preserved')
                    require(runtime.state(code).status==RUNTIME_READY,'Skill runtime readiness')
                    with zipfile.ZipFile(io.BytesIO(raw)) as z:
                        for name in z.namelist():require((runtime.root/name).read_bytes()==z.read(name),'Skill runtime stale content')
                if case['sharing']=='yes':
                    for key,before in shared_before.items():
                        row=await stores.agent.get_by_id(IDS[key])
                        after={k:getattr(row,k) for k in before}
                        require(after==before,'shared Agent association changed')
                        shared_spec=AgentSpec(name=row.name,model='test',instructions=row.prompt,capabilities=row.capabilities,skills=row.skills)
                        effective=await load_effective_ui_tools(stores,shared_spec)
                        expected_shared=set()
                        for code in fixture.existing_ui:
                            expected_shared.update(t['name'] for t in fixture.existing_ui[code]['tools'])
                            expected_shared.update(t['name'] for t in fixture.incoming_ui.get(code,{}).get('tools',[]))
                        require({t.name for t in effective.definitions}==expected_shared,'shared whole/partial runtime scope changed')
                async with stores.agent._read() as db:
                    stored_ui=list((await db.execute(select(UiToolDefinition))).scalars())
                    stored_agents=list((await db.execute(select(Agent))).scalars())
                    stored_skills=list((await db.execute(select(SkillPackage))).scalars())
                    stored_packages=list((await db.execute(select(UiToolPackage))).scalars())
                    require(len(stored_agents)==1+(2 if case['sharing']=='yes' else 0),'exact Agent total')
                    require({r.code for r in stored_skills}==set(fixture.incoming_skills)|set(fixture.existing_skills),'exact Skill total and codes')
                    require({r.code for r in stored_packages}==set(fixture.incoming_ui)|set(fixture.existing_ui),'exact UI package total and codes')
                    expected_global_tools={t['name'] for p in [*fixture.incoming_ui.values(),*fixture.existing_ui.values()] for t in p['tools']}
                    require({r.name for r in stored_ui}==expected_global_tools,'exact global UI tools union')
                    for code,old in fixture.existing_ui.items():
                        extras=[t for t in old['tools'] if t['name'] not in {x['name'] for x in fixture.incoming_ui[code]['tools']}]
                        for extra in extras:
                            kept=next((t for t in stored_ui if t.name==extra['name']),None)
                            require(kept is not None and kept.description==extra['description'],'unpackaged extra UI tool lost or changed')
                    if case['mode']=='overwrite':
                        binding=await db.get(IntegrationSettings,'matrix-binding')
                        require(binding.settings=={'agent_id':IDS['target']},'overwrite broke integration binding')
                exported=await client.get(f'/api/v1/agents/{agent["id"]}/export')
                require(exported.status_code==200,f'export HTTP {exported.status_code}')
                parsed=archive.parse_package(exported.content)
                require(parsed.agent.prompt==fixture.agent['prompt'],'export prompt')
                require('ui_tool_selection' not in parsed.agent.model_dump(),'export omits old UI selection')
                require(set(parsed.skills)==set(fixture.incoming_skills),'export Skill inclusion')
                for code,(raw,_) in parsed.skills.items():require(raw==fixture.incoming_skills[code],'export Skill bytes')
                return {'case_id':case['case_id'],**{k:case[k] for k in DIMENSIONS},'status':'passed','checks':checks,
                    'duration_ms':round((time.perf_counter()-began)*1000,2),'missing_count':len(result['missing']),
                    'expected':{'resource_statuses':{f'{k[0]}:{k[1]}':v for k,v in fixture.statuses.items()},'model_id':fixture.expected_model,'ui_tools':sorted(expected_names),'install_actions':expected_actions,'tool_actions':fixture.tool_statuses},
                    'actual':{'resource_statuses':{f'{k[0]}:{k[1]}':v for k,v in actual_status.items()},'model_id':agent['model_id'],'ui_tools':sorted(t.name for t in loaded.definitions),'install_actions':actual_actions,'tool_actions':actual_tool_actions}}
        finally:
            archive.BUILTIN_PACKAGES_ROOT=original_builtin
            reset_context()
            await ctx.aclose()
