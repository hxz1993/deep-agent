"""Administrator Agent package export, preflight and explicit installation."""
from __future__ import annotations

import asyncio
from typing import Annotated, Literal

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, ValidationError

from deep_agent.agent_packages import archive
from deep_agent.agent_packages.archive import InstallOptions, PackageConflict, PackageError
from deep_agent.agent_packages.service import PackageOutcomeUnknown, export_package, install_package, preview_package
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.agent.headers import extract_forward_headers
from deep_agent.core.context import AppContextDep
from deep_agent.db.ids import normalize_agent_id
from deep_agent.schemas.agent import AgentResponse

router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)
tag = 'agent-packages'


class AgentPackageInstallResponse(BaseModel):
    agent: AgentResponse
    mode: Literal['create', 'overwrite']
    results: list[dict]
    missing: list[dict]


def package_error(exc):
    if isinstance(exc, PackageOutcomeUnknown):
        return HTTPException(503, detail={'message': str(exc), 'result_uncertain': True})
    if isinstance(exc, PackageConflict):
        return HTTPException(409, detail=str(exc))
    if isinstance(exc, KeyError):
        return HTTPException(404, detail='Agent 或安装包不存在')
    if isinstance(exc, ValidationError):
        return HTTPException(422, detail={'message': '安装包字段不符合格式要求', 'errors': exc.errors(include_input=False, include_context=False)})
    return HTTPException(422, detail=str(exc))


async def source(file, builtin_code):
    if (file is not None) == (builtin_code is not None):
        raise PackageError('上传文件与自带安装包编码必须二选一')
    if file is not None:
        raw = await file.read(archive.MAX_PACKAGE_BYTES + 1)
    else:
        try:
            raw = await asyncio.to_thread(archive.read_builtin, builtin_code)
        except OSError as exc:
            raise PackageError('自带安装包无法读取') from exc
    return await asyncio.to_thread(archive.parse_package, raw)


@router.get('/agent-packages', summary='列出自带 Agent 安装包')
async def list_agent_packages():
    items, errors = [], []
    root = archive.BUILTIN_PACKAGES_ROOT
    for package_file in sorted(root.iterdir()) if root.exists() else []:
        if package_file.suffix != '.zip' or (not package_file.is_file() and not package_file.is_symlink()):
            continue
        try:
            p = await source(None, package_file.stem)
            items.append({'code': package_file.stem, 'name': p.agent.name,
                          'description': f'{len(p.agent.skills)} Skill · {sum(len(u.tools) for u in p.ui)} UI 工具',
                          'manifest': p.manifest.model_dump()})
        except (ValueError, OSError) as exc:
            errors.append({'code': package_file.stem, 'message': '安装包无效或不可读取'})
    return {'items': items, 'errors': errors}


@router.get('/agents/{agent_id}/export', summary='导出 Agent 安装包')
async def export_agent_package(agent_id: str, request: Request, ctx: AppContextDep):
    try:
        raw = await export_package(ctx, normalize_agent_id(agent_id), extract_forward_headers(request))
    except (ValueError, KeyError) as exc:
        raise package_error(exc) from exc
    return Response(raw, media_type='application/zip', headers={
        'Content-Disposition': 'attachment; filename="deepagent-agent-package.zip"',
        'Cache-Control': 'no-store',
    })


@router.post('/agent-packages/preview', summary='预检 Agent 安装包')
async def preview_agent_package(
    ctx: AppContextDep,
    file: Annotated[UploadFile | None, File()] = None,
    builtin_code: Annotated[str | None, Form(max_length=64)] = None,
    mode: Annotated[str, Form()] = 'create',
    target_agent_id: Annotated[str | None, Form(max_length=64)] = None,
):
    try:
        p = await source(file, builtin_code)
        return await preview_package(ctx, p, mode, target_agent_id)
    except (ValueError, KeyError) as exc:
        raise package_error(exc) from exc
    finally:
        if file is not None:
            await file.close()


@router.post('/agent-packages/install', response_model=AgentPackageInstallResponse,
             summary='安装 Agent 安装包', status_code=201)
async def install_agent_package(
    request: Request, ctx: AppContextDep,
    options: Annotated[str, Form(max_length=65536)],
    file: Annotated[UploadFile | None, File()] = None,
    builtin_code: Annotated[str | None, Form(max_length=64)] = None,
):
    try:
        selection = InstallOptions.model_validate(archive.json_read(options))
        p = await source(file, builtin_code)
        return await install_package(ctx, p, selection, extract_forward_headers(request))
    except (ValueError, KeyError) as exc:
        raise package_error(exc) from exc
    finally:
        if file is not None:
            await file.close()
