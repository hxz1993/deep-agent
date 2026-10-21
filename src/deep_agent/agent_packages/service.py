from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import lazyload

from deep_agent import __version__
from deep_agent.agent_packages.archive import (
    InstallOptions, Package, PackageAgent, PackageConflict, PackageError,
    PackageUi, build_zip, canonical, digest, parse_package,
)
from deep_agent.agent_packages.runtime_transaction import RuntimeCheckpoint
from deep_agent.capabilities.ui.builtin import builtin_ui_records
from deep_agent.db.ids import new_agent_id
from deep_agent.db.models import (
    Agent, AgentOutputConfig, LlmModel, LlmProvider, McpServer, SkillPackage, UiToolDefinition, UiToolPackage,
)
from deep_agent.db.reference_integrity import is_unique_conflict
from deep_agent.db.stores.agent.base import save_output_config, trusted_updater
from deep_agent.models.provider_registry import model_unavailable_reason
from deep_agent.schemas.agent import AgentResponse
from deep_agent.schemas.ui_tools import UiToolCreate
from deep_agent.skills.catalog import get_builtin_skill, list_builtin_skills
from deep_agent.skills.runtime import RUNTIME_READY, skill_platform_file_headers
from deepfos_platform.client.platform import FILE_TYPE_SKILL_PACKAGE


def ui_payload(package, tools):
    return {
        'code': package.code, 'label': package.label, 'description': package.description,
        'tools': [UiToolCreate(
            name=t.name, package_code=t.package_code, description=t.description,
            parameters_json_schema=t.parameters_json_schema, widget=t.widget,
            result_schema=t.result_schema, metadata=t.metadata_,
        ).model_dump() for t in sorted(tools, key=lambda t: t.name)],
    }


async def snapshot(db):
    result = {}
    for cls in (Agent, AgentOutputConfig, LlmModel, LlmProvider, McpServer, SkillPackage, UiToolPackage, UiToolDefinition):
        result[cls] = list((await db.execute(select(cls).options(lazyload('*')).execution_options(populate_existing=True))).scalars())
    return result


def snapshot_hash(data):
    # Hash configuration state; credentials never leave this function in plaintext.
    tables = {}
    for cls, rows in data.items():
        tables[cls.__tablename__] = sorted(
            [digest({column.key: str(getattr(row, 'metadata_' if column.key == 'metadata' else column.key))
                     for column in cls.__table__.columns if not (cls is Agent and column.key == 'ui_tool_selection')}) for row in rows]
        )
    return digest(tables)


def preview_from_snapshot(ctx, package: Package, data, mode='create', target_agent_id=None):
    if mode not in ('create', 'overwrite') or (mode == 'overwrite') != bool(target_agent_id):
        raise PackageError('请选择新建，或选择要覆盖的 Agent')
    target = next((a for a in data[Agent] if a.id == target_agent_id), None)
    if mode == 'overwrite' and target is None:
        raise PackageConflict('目标 Agent 已不存在，请重新选择并预检')
    providers = {p.id: p for p in data[LlmProvider]}
    models = sorted([
        {'id': m.id, 'name': m.name, 'provider_type': providers[m.provider_id].provider_type}
        for m in data[LlmModel] if not model_unavailable_reason(m, providers.get(m.provider_id))
    ], key=lambda m: (m['name'], m['id']))
    mcps = sorted([{'code': f'mcp:{m.name}', 'name': m.name} for m in data[McpServer]], key=lambda m: m['code'])
    for code in sorted(ctx.registry.codes()):
        if code.startswith('mcp:') and not any(x['code'] == code for x in mcps):
            mcps.append({'code': code, 'name': code[4:]})
    builtin_skills = {s.code for s in list_builtin_skills()}
    uploaded = {s.code: s for s in data[SkillPackage]}
    packages = {p.code: p for p in data[UiToolPackage]}
    builtin_ui = {t.package_code for t in builtin_ui_records()}
    builtin_names = {t.name for t in builtin_ui_records()}
    available = set(ctx.registry.codes()) | builtin_ui
    resources, blocked = [], []
    if package.agent.output_config is not None:
        output_name = package.agent.output_config.name
        known_names = {tool.name for incoming in package.ui for tool in incoming.tools}
        known_names.update(tool.name for tool in builtin_ui_records()
                           if tool.package_code in package.agent.capabilities)
        known_names.update(tool.name for tool in data[UiToolDefinition]
                           if tool.package_code in package.agent.capabilities)
        selected = getattr(ctx.registry, 'selected', None)
        if selected is not None:
            known_names.update(tool.name for resource in selected(package.agent.capabilities) for tool in resource.tools)
        if output_name in known_names:
            blocked.append('输出编码与已关联工具名称冲突')
    def add(kind, code, label, status, **extra):
        refs = []
        if status == 'update':
            refs = [{'id': a.id, 'name': a.name} for a in data[Agent]
                    if a.id != target_agent_id and code in ((a.skills or []) if kind == 'skill' else (a.capabilities or []))]
        row = {'key': f'{kind}:{code}', 'kind': kind, 'code': code, 'label': label,
               'status': status, 'affected_agents': sorted(refs, key=lambda a: a['id']), **extra}
        resources.append(row)
        return row
    for code in package.agent.skills:
        if code in package.skills:
            _, incoming = package.skills[code]
            existing = uploaded.get(code)
            if code in builtin_skills:
                add('skill', code, incoming['label'], 'blocked')
                blocked.append(f'Skill {code} 是内置资源，不能覆盖')
            else:
                same = existing and existing.content_hash == incoming['content_hash']
                add('skill', code, incoming['label'], 'reuse' if same else 'update' if existing else 'new')
        else:
            add('builtin_skill', code, code, 'reuse' if code in builtin_skills else 'missing')
    for incoming in package.ui:
        existing = packages.get(incoming.code)
        tools = [t for t in data[UiToolDefinition] if t.package_code == incoming.code]
        local_tools = {t.name: t for t in tools}
        incoming_names = {t.name for t in incoming.tools}
        collisions = [t.name for t in incoming.tools if t.name in builtin_names or any(
            old.name == t.name and old.package_code != incoming.code for old in data[UiToolDefinition])]
        actions = []
        for tool in incoming.tools:
            old = local_tools.get(tool.name)
            same = False
            if old:
                try:
                    same = canonical(ui_payload(existing, [old])['tools'][0]) == canonical(tool.model_dump())
                except ValueError:
                    pass
            actions.append({'name': tool.name, 'status': 'reuse' if same else 'update' if old else 'new'})
        same_meta = existing and existing.label == incoming.label and existing.description == incoming.description
        status = 'reuse' if same_meta and all(t['status'] == 'reuse' for t in actions) else 'update' if existing else 'new'
        if collisions or incoming.code in builtin_ui or (existing and existing.source == 'builtin'):
            status = 'blocked'
            blocked.append(f"UI 工具包 {incoming.label} 存在内置或跨包工具名冲突：{'、'.join(collisions) or incoming.code}")
        add('ui', incoming.code, incoming.label, status, tools=actions,
            retained_tools=sorted(set(local_tools) - incoming_names))
    packed_ui = {p.code for p in package.ui}
    for code in package.agent.capabilities:
        if not code.startswith('mcp:') and code not in packed_ui:
            add('builtin_tool', code, code, 'reuse' if code in available else 'missing')
    reference = package.agent.model_reference
    matches = [m for m in models if reference and m['name'] == reference.name]
    refs = {
        'model': {'reference': reference.model_dump() if reference else None,
                  'matched_id': (matches or models)[0]['id'] if models else None},
        'mcps': [{'code': c, 'matched_code': c if any(x['code'] == c for x in mcps) else None}
                 for c in package.agent.capabilities if c.startswith('mcp:')],
    }
    resulting_caps = {r['code'] for r in resources if r['kind'] in ('ui', 'builtin_tool') and r['status'] not in ('missing', 'blocked')}
    resulting_caps.update(r['matched_code'] for r in refs['mcps'] if r['matched_code'])
    resulting_skills = {r['code'] for r in resources if r['kind'] in ('skill', 'builtin_skill') and r['status'] not in ('missing', 'blocked')}
    removed = []
    if target:
        removed.extend({'kind': 'capability', 'code': c, 'label': c, 'status': 'unlinked'} for c in target.capabilities or [] if c not in resulting_caps)
        removed.extend({'kind': 'skill', 'code': c, 'label': c, 'status': 'unlinked'} for c in target.skills or [] if c not in resulting_skills)
    return {'digest': digest({'package': package.digest, 'target': snapshot_hash(data),
                             'builtin': sorted(available | builtin_skills), 'mode': mode, 'target_agent_id': target_agent_id}),
            'manifest': package.manifest.model_dump(), 'agent': package.agent.model_dump(),
            'mode': mode, 'target_agent': {'id': target.id, 'name': target.name} if target else None,
            'structured_output': {
                'enabled': package.agent.structured_output_enabled,
                'name': package.agent.output_config.name if package.agent.output_config else None,
                'target_enabled': bool(target.structured_output_enabled) if target else False,
                'action': 'replace' if package.agent.structured_output_enabled else 'disable',
            },
            'resources': resources, 'references': refs, 'models': models, 'mcps': mcps,
            'removed_associations': removed, 'blocked': blocked}


async def preview_package(ctx, package, mode='create', target_agent_id=None):
    async with ctx.stores.agent._read() as db:
        return preview_from_snapshot(ctx, package, await snapshot(db), mode, target_agent_id)


def validate_options(preview, options):
    if preview['digest'] != options.digest:
        raise PackageConflict('安装包、目标 Agent 或环境资源已变化，请重新预检')
    if options.model_id and options.model_id not in {m['id'] for m in preview['models']}:
        raise PackageConflict('选择的模型不可用，请重新预检')
    if preview['blocked']:
        raise PackageConflict('；'.join(preview['blocked']))


async def export_package(ctx, agent_id, headers):
    async with ctx.stores.agent._read() as db:
        row = (await db.execute(select(Agent).where(Agent.id == agent_id).options(lazyload('*')))).scalar_one_or_none()
        if row is None:
            raise KeyError('Agent 不存在')
        data = await snapshot(db)
        source_hash = snapshot_hash(data)
        source_agent = digest({'name': row.name, 'prompt': row.prompt,
                               'capabilities': row.capabilities, 'skills': row.skills, 'model_id': row.model_id})
        models = {m.id: m for m in data[LlmModel]}
        providers = {p.id: p for p in data[LlmProvider]}
        model = models.get(row.model_id)
        provider = providers.get(model.provider_id) if model else None
        if row.model_id and (not model or not provider):
            raise PackageError('Agent 引用的模型或 Provider 已缺失')
        agent = PackageAgent(name=row.name, prompt=row.prompt,
                             capabilities=row.capabilities, skills=row.skills,
                             model_reference={'name': model.name, 'provider_type': provider.provider_type} if model else None,
                             structured_output_enabled=bool(row.structured_output_enabled),
                             output_config=(next(({
                                 'name': c.name, 'description': c.description, 'json_schema': c.json_schema,
                             } for c in data[AgentOutputConfig] if c.agent_id == row.id), None)
                                            if row.structured_output_enabled else None))
        custom_ui = []
        builtin_ui = {t.package_code for t in builtin_ui_records()}
        for code in agent.capabilities:
            if not code.startswith('ui:') or code in builtin_ui:
                continue
            p = next((p for p in data[UiToolPackage] if p.code == code), None)
            if p is None:
                raise PackageError('Agent 引用的 UI 工具包已缺失')
            if p.source == 'builtin':
                continue
            custom_ui.append(PackageUi.model_validate(ui_payload(p, [t for t in data[UiToolDefinition] if t.package_code == code])).validated().model_dump())
        uploaded = {s.code: s for s in data[SkillPackage]}
        needed = []
        for code in agent.skills:
            if get_builtin_skill(code):
                continue
            if code not in uploaded:
                raise PackageError('Agent 引用的 Skill 原包已缺失')
            needed.append(uploaded[code])
    # A disabled Agent must produce an actual old-format ZIP, with no new false
    # or null keys; retained administrative drafts are never portable resources.
    exported_agent = agent.model_dump(exclude=(
        {'structured_output_enabled', 'output_config'} if not agent.structured_output_enabled else set()
    ))
    files = {'manifest.json': canonical({'format': 'deepagent-agent-package', 'schema_version': 1, 'exported_from': __version__}),
             'agent.json': canonical(exported_agent), 'ui-tools.json': canonical(custom_ui)}
    for skill in needed:
        try:
            payload = await ctx.skill_runtime.download_package(skill, request_headers=headers)
        except Exception as exc:
            raise PackageError(f'Skill {skill.code} 原包不可用，无法完整导出') from exc
        files[f'skills/{skill.code}.zip'] = payload
    async with ctx.stores.agent._read() as db:
        after = (await db.execute(select(Agent).where(Agent.id == agent_id).options(lazyload('*')))).scalar_one_or_none()
        if not after or source_hash != snapshot_hash(await snapshot(db)) or source_agent != digest({
            'name': after.name, 'prompt': after.prompt,
            'capabilities': after.capabilities, 'skills': after.skills, 'model_id': after.model_id,
        }):
            raise PackageConflict('导出期间配置发生变化，请重新导出')
    raw = await asyncio.to_thread(build_zip, files)
    await asyncio.to_thread(parse_package, raw)
    return raw


class PackageOutcomeUnknown(PackageError):
    """The database outcome could not be confirmed; never claim rollback."""


def agent_state(row):
    if row is None:
        return None
    return digest({k: getattr(row, k) for k in
                   ('id', 'name', 'prompt', 'model_id', 'capabilities', 'skills', 'structured_output_enabled', 'updated_by')})


def installation_results(preview, agent):
    results = [{'kind': 'agent', 'code': agent.id, 'label': agent.name,
                'status': 'new' if preview['mode'] == 'create' else 'update'}]
    results.extend(preview['resources'])
    results.extend({'kind': 'mcp', 'code': r['code'], 'label': r['code'],
                    'status': 'reuse' if r['matched_code'] else 'missing'} for r in preview['references']['mcps'])
    results.extend(preview['removed_associations'])
    return {'agent': agent, 'mode': preview['mode'], 'results': results,
            'missing': [r for r in results if r['status'] == 'missing']}


async def _install(ctx, package, options, headers, *, user_id=None):
    preview = await preview_package(ctx, package, options.mode, options.target_agent_id)
    validate_options(preview, options)
    model_id = options.model_id if 'model_id' in options.model_fields_set else preview['references']['model']['matched_id']
    file_headers = skill_platform_file_headers(headers)
    new_files, prepared, existing_payloads = [], {}, {}
    checkpoint = RuntimeCheckpoint(ctx.skill_runtime)
    original_skills, original_agent, desired_agent = {}, None, None
    original_config, desired_config = None, None
    response, committed, preserve_checkpoint = None, False, False
    agent_id = options.target_agent_id or new_agent_id()
    try:
        async with ctx.stores.agent._read() as db:
            existing = {s.code: s for s in (await db.execute(select(SkillPackage))).scalars()}
            original_skills = {c: (r.platform_file_id, r.content_hash) for c, r in existing.items()}
            original_agent = agent_state(await db.get(Agent, agent_id))
        # External file I/O precedes the configuration transaction.
        for resource in preview['resources']:
            if resource['kind'] != 'skill':
                continue
            code = resource['code']
            if resource['status'] in ('new', 'update'):
                payload, metadata = package.skills[code]
                try:
                    stored = await ctx.file_store.upload(file_type=FILE_TYPE_SKILL_PACKAGE,
                        file_name=f'{code}.zip', mime='application/zip', data=payload, request_headers=file_headers)
                except Exception as exc:
                    raise PackageError(f'Skill {code} 文件保存失败，安装未完成') from exc
                new_files.append(stored.id)
                prepared[code] = {**metadata, 'platform_file_id': stored.id,
                                  'file_name': f'{code}.zip', 'file_size': len(payload)}
            else:
                try:
                    existing_payloads[code] = await ctx.skill_runtime.download_package(existing[code], request_headers=headers)
                except Exception as exc:
                    raise PackageError(f'Skill {code} 原包不可用，安装未完成') from exc
        async with ctx.stores.agent._write(exclusive=True) as db:
            try:
                data = await snapshot(db)
                original_config = snapshot_hash(data)
                current = preview_from_snapshot(ctx, package, data, options.mode, options.target_agent_id)
                validate_options(current, options)
                if (await db.execute(select(Agent.id).where(Agent.name == options.name, Agent.id != agent_id))).first():
                    raise PackageConflict('Agent 名称已存在，请修改名称')
                caps, skills = [], []
                for r in current['resources']:
                    if r['status'] in ('missing', 'blocked'):
                        continue
                    code = r['code']
                    if r['kind'] in ('skill', 'builtin_skill'):
                        skills.append(code)
                        if r['kind'] == 'builtin_skill':
                            continue
                        if code in prepared:
                            row = await db.get(SkillPackage, code)
                            if row is None:
                                row = SkillPackage(**prepared[code]); db.add(row)
                            else:
                                for key, value in prepared[code].items():
                                    setattr(row, key, value)
                            payload = package.skills[code][0]
                        else:
                            row = await db.get(SkillPackage, code)
                            payload = existing_payloads[code]
                        if not ctx.skill_runtime._ready_from_disk(row):
                            await checkpoint.capture(code)
                            ready = await ctx.skill_runtime.materialize(row, payload=payload, request_headers=headers)
                            if ready.status != RUNTIME_READY:
                                raise PackageError(f'Skill {code} 运行资源准备失败，安装未完成')
                    else:
                        caps.append(code)
                        if r['kind'] == 'ui' and r['status'] in ('new', 'update'):
                            incoming = next(p for p in package.ui if p.code == code)
                            row = await db.get(UiToolPackage, code)
                            if row is None:
                                row = UiToolPackage(code=code, source='manual'); db.add(row)
                            row.label, row.description = incoming.label, incoming.description
                            for tool in incoming.tools:
                                values = tool.model_dump()
                                values['metadata_'] = values.pop('metadata')
                                old = await db.get(UiToolDefinition, tool.name)
                                if old is None:
                                    db.add(UiToolDefinition(**values, source='manual'))
                                else:
                                    for key, value in values.items():
                                        setattr(old, key, value)
                caps.extend(r['matched_code'] for r in current['references']['mcps'] if r['matched_code'])
                row = await db.get(Agent, agent_id) if options.mode == 'overwrite' else Agent(id=agent_id)
                row.name, row.prompt, row.model_id = options.name, package.agent.prompt, model_id
                row.capabilities, row.skills = list(dict.fromkeys(caps)), list(dict.fromkeys(skills))
                row.ui_tool_selection = {}
                row.structured_output_enabled = package.agent.structured_output_enabled
                row.updated_by = trusted_updater(user_id)
                row.updated_at = datetime.now(UTC)
                if options.mode == 'create':
                    db.add(row)
                await db.flush()
                await save_output_config(db, agent_id, package.agent.output_config)
                await db.flush()
                await db.refresh(row)
                desired_agent = agent_state(row)
                model = await db.get(LlmModel, row.model_id) if row.model_id else None
                response = installation_results(current, AgentResponse.from_row(row, model))
                desired_config = snapshot_hash(await snapshot(db))
            except BaseException:
                # No COMMIT has occurred: restore runtime while holding the write barrier.
                for code in list(checkpoint.saved):
                    await checkpoint.restore(code)
                raise
        committed = True
        return response
    except BaseException as exc:
        try:
            async with ctx.stores.agent._write(exclusive=True) as db:
                actual_agent = agent_state(await db.get(Agent, agent_id))
                actual_config = snapshot_hash(await snapshot(db))
                if desired_config and actual_config == desired_config:
                    # COMMIT succeeded but its acknowledgement failed.
                    committed = True
                    return response
                for code in list(checkpoint.saved):
                    current_row = await db.get(SkillPackage, code)
                    version = (current_row.platform_file_id, current_row.content_hash) if current_row else None
                    if version == original_skills.get(code):
                        await checkpoint.restore(code)
                referenced = set((await db.execute(select(SkillPackage.platform_file_id))).scalars())
                for file_id in new_files:
                    if file_id not in referenced:
                        try:
                            await ctx.file_store.delete(file_id, file_headers)
                        except Exception as cleanup_error:
                            logger.warning('Agent package file cleanup failed file_id={} error_type={}', file_id, type(cleanup_error).__name__)
                if desired_config and actual_config != original_config:
                    raise PackageOutcomeUnknown('安装结果待确认，请刷新 Agent 列表核对后再操作')
        except PackageOutcomeUnknown:
            preserve_checkpoint = True
            raise
        except Exception as verification_error:
            preserve_checkpoint = True
            logger.warning('Agent package outcome unknown error_type={}', type(verification_error).__name__)
            raise PackageOutcomeUnknown('安装结果待确认，请刷新 Agent 列表核对后再操作') from exc
        if isinstance(exc, IntegrityError) and is_unique_conflict(exc):
            raise PackageConflict('资源或名称发生并发冲突，请重新预检') from exc
        if not isinstance(exc, (PackageError, asyncio.CancelledError)):
            raise PackageError('安装写入失败，本次数据库变更已回滚') from exc
        raise
    finally:
        if not preserve_checkpoint:
            try:
                await checkpoint.discard()
            except Exception as cleanup_error:
                # A temporary directory is not part of the installation's
                # readiness. Do not turn a confirmed COMMIT into a false failure.
                logger.warning('Agent package temporary cleanup deferred error_type={}', type(cleanup_error).__name__)


async def install_package(ctx, package, options, headers, *, user_id=None):
    # An HTTP disconnect is not a request to cancel a partly committed installation.
    task = asyncio.create_task(_install(ctx, package, options, headers, user_id=user_id))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        try:
            await task
        finally:
            raise
