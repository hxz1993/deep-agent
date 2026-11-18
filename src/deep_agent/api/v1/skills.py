from __future__ import annotations

import asyncio
import hashlib
import io
import json
import re
import shutil
import zipfile
from datetime import UTC, datetime
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from deepfos_platform.log import safe_logger as logger
from starlette.responses import Response

from deep_agent.agent.headers import extract_forward_headers
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.api.v1.integration_apps import APP_CONFIG_PATH, AppConfigAccess, app_config_access, verify_app_scope_rows
from deep_agent.api.v1.request_identity import RequestUserDep
from sqlalchemy import select
from deep_agent.api.v1.integration_routes import IntegrationRoute
from deep_agent.integrations.app_scope import GENERAL_SKILL_SCOPE, skill_package_code
from deep_agent.core.context import AppContextDep
from deep_agent.db.models import IntegrationScopedSettings, SkillPackage
from deep_agent.db.reference_integrity import ReferenceConflict
from deep_agent.schemas.skill import (
    SkillAgentReference,
    SkillDetail,
    SkillInstallResult,
    SkillPackageRecord,
    SkillResource,
    SkillResourceContent,
    SkillReloadResult,
    SkillSummary,
    SkillUpdate,
)
from deep_agent.skills.catalog import RETIRED_DIAGNOSTICS_SKILL_CODE, SkillDescriptor, get_builtin_skill, list_builtin_skills
from deep_agent.skills.package import MAX_ZIP_BYTES, ParsedSkillPackage, SkillPackageError, parse_skill_zip
from deep_agent.skills.resource_reader import (
    SkillResourceReadError,
    present_resource_entry,
    read_resource_page,
)
from deep_agent.skills.runtime import (
    RUNTIME_ERROR,
    RUNTIME_READY,
    SkillRuntimeLibrary,
    skill_platform_file_headers,
)
from deep_agent.files.storage import FileStore
from deepfos_platform.client.platform import FILE_TYPE_SKILL_PACKAGE


router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)
tag = "Skills"
app_router = APIRouter(
    prefix=APP_CONFIG_PATH + "/{app_id}",
    dependencies=[*CONSOLE_ADMIN_DEPENDENCIES, Depends(app_config_access)],
    route_class=IntegrationRoute,
)


def _skill_access(request: Request) -> AppConfigAccess | None:
    return getattr(request.state, "integration_app_access", None)


SkillAccess = Annotated[AppConfigAccess | None, Depends(_skill_access)]


def _matches_scope(row, scope_ref):
    return row is not None and (getattr(row, "scope_ref", None) or GENERAL_SKILL_SCOPE) == scope_ref



def _counts(index: list[dict]) -> tuple[int, int, int]:
    references = sum(1 for item in index if item.get("category") == "reference")
    assets = sum(1 for item in index if item.get("category") == "asset")
    scripts = sum(1 for item in index if item.get("category") == "script")
    return references, assets, scripts


def _builtin_detail(item: SkillDescriptor) -> SkillDetail:
    references, assets, scripts = _counts(item.resource_index)
    return SkillDetail(
        code=item.code,
        label=item.label,
        description=item.description,
        version=item.version,
        schema_version=item.schema_version,
        source="builtin",
        reference_count=references,
        asset_count=assets,
        script_count=scripts,
        contains_scripts=item.contains_scripts,
        runtime_status=RUNTIME_READY,
        manifest=item.manifest,
        resources=[
            SkillResource.model_validate(present_resource_entry(entry, console_preview=True))
            for entry in item.resource_index
        ],
    )


def _row_detail(
    row: SkillPackage,
    runtime: SkillRuntimeLibrary,
) -> SkillDetail:
    index = [dict(item) for item in (row.resource_index or [])]
    references, assets, scripts = _counts(index)
    runtime_state = runtime.state_for(row)
    return SkillDetail(
        code=skill_package_code(row),
        label=row.label,
        description=row.description,
        version=row.version,
        schema_version=row.schema_version,
        source="upload",
        reference_count=references,
        asset_count=assets,
        script_count=scripts,
        contains_scripts=row.contains_scripts,
        file_name=row.file_name,
        file_size=row.file_size,
        content_hash=row.content_hash,
        runtime_status=runtime_state.status,
        runtime_error_code=runtime_state.error_code,
        runtime_synced_at=runtime_state.synced_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
        manifest=dict(row.manifest or {}),
        resources=[
            SkillResource.model_validate(present_resource_entry(entry, console_preview=True))
            for entry in index
        ],
    )


def _summary(detail: SkillDetail) -> SkillSummary:
    return SkillSummary.model_validate(detail.model_dump(exclude={"manifest", "resources"}))


async def _row_or_404(ctx: AppContextDep, code: str, scope: AppConfigAccess | None = None) -> SkillPackage:
    if scope is None:
        row = await ctx.stores.skill_package.get_by_code(code, scope_ref=GENERAL_SKILL_SCOPE)
    elif scope.skill_scope is not None and code in scope.skill_codes:
        packages = await ctx.stores.skill_package.list_app_packages(scope.skill_scope, (code,))
        row = packages[0] if packages else None
    else:
        row = None
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Skill not found: {code}")
    return row


async def _storage_key(ctx: AppContextDep, code: str, scope: AppConfigAccess | None) -> tuple[str, str]:
    if scope is None:
        return code, GENERAL_SKILL_SCOPE
    row = await _row_or_404(ctx, code, scope)
    return row.code, row.scope_ref or GENERAL_SKILL_SCOPE


def _reject_builtin(code: str) -> None:
    if get_builtin_skill(code) is not None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Builtin Skill is read-only: {code}",
        )


async def _read_zip(file: UploadFile) -> tuple[bytes, str]:
    raw = await file.read(MAX_ZIP_BYTES + 1)
    if len(raw) > MAX_ZIP_BYTES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Skill ZIP exceeds {MAX_ZIP_BYTES} bytes",
        )
    return raw, file.filename or "skill.zip"


def _record(
    parsed: ParsedSkillPackage,
    *,
    platform_file_id: str,
    file_name: str,
    file_size: int,
) -> SkillPackageRecord:
    return SkillPackageRecord(
        code=parsed.code,
        label=parsed.label,
        description=parsed.description,
        version=parsed.version,
        schema_version=parsed.schema_version,
        source="upload",
        platform_file_id=platform_file_id,
        file_name=file_name,
        file_size=file_size,
        content_hash=parsed.content_hash,
        manifest=parsed.manifest,
        resource_index=parsed.resource_index,
        contains_scripts=parsed.contains_scripts,
    )


async def _upload_file(
    request: Request,
    store: FileStore,
    *,
    raw: bytes,
    file_name: str,
):
    headers = skill_platform_file_headers(extract_forward_headers(request))
    try:
        uploaded = await store.upload(
            file_type=FILE_TYPE_SKILL_PACKAGE,
            file_name=file_name,
            mime="application/zip",
            data=raw,
            request_headers=headers,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Skill file upload failed: {type(exc).__name__}",
        ) from exc
    return headers, uploaded


async def _best_effort_delete(
    store: FileStore,
    headers: dict[str, str],
    file_id: str,
    *,
    context: str,
) -> None:
    try:
        await store.delete(file_id, headers)
    except Exception:
        logger.opt(exception=True).warning(
            "Orphan platform Skill file cleanup failed context={} file_id={}",
            context,
            file_id,
        )


async def _materialize_current(ctx, code, *, request_headers, payload=None, expected=None, force=False, scope_ref=GENERAL_SKILL_SCOPE):
    async with ctx.skill_runtime.coordinated_row(ctx.stores, code) as row:
        if not _matches_scope(row, scope_ref):
            raise HTTPException(status_code=404, detail="Skill not found")
        if expected is not None and (row.platform_file_id, row.content_hash) != (expected.platform_file_id, expected.content_hash):
            raise HTTPException(status_code=409, detail="Skill changed during runtime preparation; refresh and retry")
        state = await ctx.skill_runtime.materialize(row, request_headers=request_headers, payload=payload, force=force)
        return row, state


@app_router.get("/skills", response_model=list[SkillSummary])
@router.get("/skills", response_model=list[SkillSummary])
async def list_skills(ctx: AppContextDep, scope: SkillAccess = None) -> list[SkillSummary]:
    if scope is not None and scope.skill_scope is None:
        return []
    details = [_builtin_detail(item) for item in list_builtin_skills()] if scope is None else []
    store = ctx.stores.skill_package
    rows = await store.list_app_packages(scope.skill_scope, scope.skill_codes) if scope else await store.list_all()
    details.extend(_row_detail(row, ctx.skill_runtime) for row in rows)
    return [_summary(item) for item in sorted(details, key=lambda value: (value.source != "builtin", value.label, value.code))]


@router.get("/skills/references/agents", response_model=list[SkillAgentReference])
async def list_skill_agent_references(ctx: AppContextDep) -> list[SkillAgentReference]:
    return [SkillAgentReference.model_validate(row) for row in await ctx.stores.agent.list_skill_references()]


@router.get("/skills/export")
async def export_uploaded_skills(request: Request, ctx: AppContextDep) -> Response:
    rows = sorted(await ctx.stores.skill_package.list_all(), key=lambda row: row.code)
    return await _export_skill_bundle(request, ctx, rows)


@router.get("/configuration/skills/export")
async def export_configuration_skills(request: Request, ctx: AppContextDep, user: RequestUserDep) -> Response:
    async with ctx.stores.skill_package._read() as db:
        scopes = list((await db.scalars(select(IntegrationScopedSettings))).all())
        rows = list((await db.scalars(select(SkillPackage).order_by(SkillPackage.code))).all())
    await verify_app_scope_rows(
        request, user, [{"id": row.id, "scope_key": row.scope_key, "tenant_id": row.tenant_id} for row in scopes],
        skill_scopes=(row.scope_ref for row in rows),
    )
    return await _export_skill_bundle(request, ctx, rows, configuration=True)


async def _export_skill_bundle(request, ctx, rows, *, configuration=False) -> Response:
    if not rows:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No uploaded Skills to export",
        )

    exported_at = datetime.now(UTC)
    request_headers = extract_forward_headers(request)
    packages: list[tuple[SkillPackage, bytes]] = []
    for row in rows:
        try:
            raw = await ctx.skill_runtime.download_package(
                row,
                request_headers=request_headers,
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "message": "Platform Skill export failed",
                    "skill_code": row.code,
                    "error_type": type(exc).__name__,
                },
            ) from exc
        packages.append((row, raw))

    manifest = {
        "format": "deepagent-skill-export",
        "schema_version": "1.1" if configuration else "1.0",
        "exported_at": exported_at.isoformat(),
        "skills": [
            {
                "code": row.code,
                "label": row.label,
                "description": row.description,
                "version": row.version,
                "schema_version": row.schema_version,
                "package_file": f"skills/{row.code}.zip",
                "content_hash": row.content_hash,
                "original_file_name": row.file_name,
                **({"scope_ref": row.scope_ref} if configuration else {}),
            }
            for row, _raw in packages
        ],
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "manifest.json",
            json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        )
        for row, raw in packages:
            archive.writestr(f"skills/{row.code}.zip", raw)

    prefix = "deepagent-configuration-skills" if configuration else "deepagent-skills-export"
    filename = f"{prefix}-{exported_at.strftime('%Y%m%dT%H%M%SZ')}.zip"
    return Response(
        content=output.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}", "Cache-Control": "no-store"},
    )


@app_router.get("/skills/{code}", response_model=SkillDetail)
@router.get("/skills/{code}", response_model=SkillDetail)
async def get_skill(code: str, ctx: AppContextDep, scope: SkillAccess = None) -> SkillDetail:
    builtin = get_builtin_skill(code) if scope is None else None
    if builtin is not None:
        return _builtin_detail(builtin)
    return _row_detail(await _row_or_404(ctx, code, scope), ctx.skill_runtime)


@app_router.post("/skills", response_model=SkillInstallResult, status_code=status.HTTP_201_CREATED)
@router.post("/skills", response_model=SkillInstallResult, status_code=status.HTTP_201_CREATED)
async def install_skill(
    request: Request,
    ctx: AppContextDep,
    file: UploadFile = File(...),
    scope: SkillAccess = None,
) -> SkillInstallResult:
    raw, filename = await _read_zip(file)
    parsed: ParsedSkillPackage | None = None
    try:
        parsed = parse_skill_zip(raw)
        if scope is None and parsed.code == RETIRED_DIAGNOSTICS_SKILL_CODE:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Skill code is retired: {parsed.code}",
            )
        if scope is None and (
            get_builtin_skill(parsed.code) is not None or await ctx.stores.skill_package.get_by_code(parsed.code)
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Skill already exists: {parsed.code}",
            )
        request_headers = extract_forward_headers(request)
        file_headers, uploaded = await _upload_file(
            request,
            ctx.file_store,
            raw=raw,
            file_name=filename,
        )
        try:
            row = await ctx.stores.skill_package.create(
                _record(
                    parsed,
                    platform_file_id=uploaded.id,
                    file_name=uploaded.file_name or filename,
                    file_size=uploaded.file_size,
                ),
                **({"app": scope.app, "expected_scope_id": scope.row.id if scope.row else None} if scope else {}),
            )
        except Exception:
            await _best_effort_delete(
                ctx.file_store,
                file_headers,
                uploaded.id,
                context="install-compensation",
            )
            raise
        row, _ = await _materialize_current(
            ctx, row.code, expected=row, request_headers=request_headers, payload=raw,
            scope_ref=getattr(row, "scope_ref", None) or GENERAL_SKILL_SCOPE,
        )
        return SkillInstallResult(
            status="installed",
            skill=_row_detail(row, ctx.skill_runtime),
        )
    except SkillPackageError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    finally:
        if parsed is not None:
            shutil.rmtree(parsed.package_root.parent, ignore_errors=True)


@app_router.put("/skills/{code}/package", response_model=SkillInstallResult)
@router.put("/skills/{code}/package", response_model=SkillInstallResult)
async def replace_skill_package(
    code: str,
    request: Request,
    ctx: AppContextDep,
    file: UploadFile = File(...),
    scope: SkillAccess = None,
) -> SkillInstallResult:
    if scope is None:
        _reject_builtin(code)
    current = await _row_or_404(ctx, code, scope)
    key, scope_ref = current.code, current.scope_ref or GENERAL_SKILL_SCOPE
    raw, filename = await _read_zip(file)
    parsed: ParsedSkillPackage | None = None
    try:
        parsed = parse_skill_zip(raw)
        if parsed.code != code:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Replacement package code must remain {code!r}",
            )
        request_headers = extract_forward_headers(request)
        same_content = parsed.content_hash == current.content_hash
        if same_content:
            try:
                await ctx.skill_runtime.download_package(
                    current,
                    request_headers=request_headers,
                )
            except Exception:  # noqa: BLE001 - explicit replacement repairs a missing source file
                logger.opt(exception=True).warning(
                    "Skill same-hash replacement will repair platform source code={}", code,
                )
            else:
                current, _ = await _materialize_current(
                    ctx, key, expected=current, request_headers=request_headers, payload=raw, scope_ref=scope_ref,
                )
                return SkillInstallResult(
                    status="noop",
                    skill=_row_detail(current, ctx.skill_runtime),
                )

        file_headers, uploaded = await _upload_file(
            request,
            ctx.file_store,
            raw=raw,
            file_name=filename,
        )
        try:
            replacement = _record(
                parsed,
                platform_file_id=uploaded.id,
                file_name=uploaded.file_name or filename,
                file_size=uploaded.file_size,
            ).model_copy(
                update={
                    **(
                        {
                            "label": current.label,
                            "description": current.description,
                        }
                        if same_content
                        else {}
                    ),
                }
            )
            row = await ctx.stores.skill_package.replace_package(
                key, replacement, scope_ref=scope_ref, expected_file_id=current.platform_file_id,
                **({"app": scope.app, "expected_scope_id": scope.row.id} if scope else {}),
            )
            if row is None:
                raise ReferenceConflict(f"Skill disappeared during package update: {code}")
        except Exception:
            await _best_effort_delete(
                ctx.file_store,
                file_headers,
                uploaded.id,
                context="update-compensation",
            )
            raise
        await _best_effort_delete(
            ctx.file_store,
            file_headers,
            current.platform_file_id,
            context="update-old-file",
        )
        row, _ = await _materialize_current(
            ctx, key, expected=row, request_headers=request_headers, payload=raw, force=True, scope_ref=scope_ref,
        )
        return SkillInstallResult(
            status="repaired" if same_content else "updated",
            skill=_row_detail(row, ctx.skill_runtime),
        )
    except SkillPackageError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except ReferenceConflict as exc:
        raise HTTPException(409, detail=str(exc)) from exc
    finally:
        if parsed is not None:
            shutil.rmtree(parsed.package_root.parent, ignore_errors=True)


@app_router.patch("/skills/{code}", response_model=SkillDetail)
@router.patch("/skills/{code}", response_model=SkillDetail)
async def update_skill(
    code: str,
    body: SkillUpdate,
    request: Request,
    ctx: AppContextDep,
    scope: SkillAccess = None,
) -> SkillDetail:
    if scope is None:
        _reject_builtin(code)
    key, scope_ref = await _storage_key(ctx, code, scope)
    row = await ctx.stores.skill_package.update(
        key, body, scope_ref=scope_ref,
        **({"app": scope.app, "expected_scope_id": scope.row.id} if scope else {}),
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Skill not found: {code}")
    return _row_detail(row, ctx.skill_runtime)


@app_router.delete("/skills/{code}")
@router.delete("/skills/{code}")
async def delete_skill(code: str, request: Request, ctx: AppContextDep, scope: SkillAccess = None) -> dict:
    if scope is None:
        _reject_builtin(code)
    row = await _row_or_404(ctx, code, scope)
    key, scope_ref = row.code, row.scope_ref or GENERAL_SKILL_SCOPE
    agents = await ctx.stores.agent.list_referencing_skill(code) if scope is None else []
    if agents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Skill is still referenced by Agents",
                "agents": [item.name for item in agents],
            },
        )
    try:
        if not await ctx.stores.skill_package.delete(
            key, scope_ref=scope_ref, expected_file_id=row.platform_file_id,
            **({"app": scope.app, "expected_scope_id": scope.row.id} if scope else {}),
        ):
            raise ReferenceConflict("Skill changed during deletion; refresh and retry")
    except ReferenceConflict as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    await cleanup_deleted_skill(ctx, request, row)
    return {"status": "deleted", "code": code}


async def cleanup_deleted_skill(ctx, request: Request, row: SkillPackage) -> None:
    code = row.code
    # A concurrent installation may have reused this code since deletion.
    # Hold the configuration barrier through cache removal, so stale cleanup
    # cannot delete runtime files belonging to the newly installed Skill.
    async with ctx.stores.skill_package._write(exclusive=True) as db:
        if await db.get(SkillPackage, code) is None:
            await ctx.skill_runtime.remove(code)
    file_headers = skill_platform_file_headers(extract_forward_headers(request))
    await _best_effort_delete(
        ctx.file_store,
        file_headers,
        row.platform_file_id,
        context="delete",
    )


@app_router.post("/skills/{code}/reload", response_model=SkillReloadResult)
@router.post("/skills/{code}/reload", response_model=SkillReloadResult)
async def reload_skill(code: str, request: Request, ctx: AppContextDep, scope: SkillAccess = None) -> SkillReloadResult:
    if scope is None:
        _reject_builtin(code)
    key, scope_ref = await _storage_key(ctx, code, scope)
    request_headers = extract_forward_headers(request)
    _, runtime_state = await _materialize_current(
        ctx, key, request_headers=request_headers, force=True, scope_ref=scope_ref,
    )
    if runtime_state.status == RUNTIME_ERROR:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "Skill runtime reload failed",
                "error_code": runtime_state.error_code,
            },
        )
    return SkillReloadResult(
        code=code,
        runtime_status=runtime_state.status,
        runtime_error_code=runtime_state.error_code,
        runtime_synced_at=runtime_state.synced_at,
    )


@app_router.get("/skills/{code}/resources", response_model=list[SkillResource])
@router.get("/skills/{code}/resources", response_model=list[SkillResource])
async def list_skill_resources(code: str, ctx: AppContextDep, scope: SkillAccess = None) -> list[SkillResource]:
    detail = await get_skill(code, ctx, scope)
    return detail.resources


@app_router.get("/skills/{code}/package")
@router.get("/skills/{code}/package")
async def download_skill_package(code: str, request: Request, ctx: AppContextDep, scope: SkillAccess = None) -> Response:
    if scope is None:
        _reject_builtin(code)
    row = await _row_or_404(ctx, code, scope)
    try:
        raw = await ctx.skill_runtime.download_package(
            row,
            request_headers=extract_forward_headers(request),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Platform Skill download failed: {type(exc).__name__}",
        ) from exc
    # Name the original bytes, without repacking or changing persisted metadata.
    version = re.sub(r"[^A-Za-z0-9._+-]+", "-", row.version or "").strip(".-_")[:64]
    if version and not version.startswith("v"):
        version = f"v{version}"
    filename = f"{code}{'-' + version if version else ''}.zip"
    return Response(
        content=raw,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename, safe='')}"},
    )


@asynccontextmanager
async def _resource_root(code: str, request: Request, ctx: AppContextDep, scope=None):
    builtin = get_builtin_skill(code) if scope is None else None
    if builtin is not None:
        yield builtin.package_root, builtin.resource_index
        return
    key, scope_ref = await _storage_key(ctx, code, scope)
    async with ctx.skill_runtime.coordinated_row(ctx.stores, key) as row:
        if not _matches_scope(row, scope_ref):
            raise HTTPException(status_code=404, detail=f"Skill not found: {code}")
        try:
            resolved = await ctx.skill_runtime.resolve(
                row,
                request_headers=extract_forward_headers(request),
            )
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=f"Could not materialize Skill package: {type(exc).__name__}",
            ) from exc
        if resolved is None:
            runtime_state = ctx.skill_runtime.state(key)
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail={
                    "message": "Skill runtime files are unavailable",
                    "error_code": runtime_state.error_code or runtime_state.status,
                },
            )
        yield resolved.package_root, resolved.resource_index


@app_router.get("/skills/{code}/resources/{path:path}", response_model=SkillResourceContent)
@router.get("/skills/{code}/resources/{path:path}", response_model=SkillResourceContent)
async def read_skill_resource(
    code: str,
    path: str,
    request: Request,
    ctx: AppContextDep,
    response: Response,
    offset: int | None = None,
    limit: int | None = None,
    sheet: str | None = None,
    char_offset: int = 0,
    raw: bool = False,
    scope: SkillAccess = None,
) -> SkillResourceContent | Response:
    preview_headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    response.headers.update(preview_headers)
    async with _resource_root(code, request, ctx, scope) as (root, index):
        entry = next((item for item in index if item.get("path") == path), None)
        if path == "SKILL.md":
            target = root / "SKILL.md"
            if not target.is_file():
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill resource not found")
            try:
                content_hash = hashlib.sha256(target.read_bytes()).hexdigest()
            except OSError as exc:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill resource not found") from exc
            entry = {
                "path": "SKILL.md",
                "category": "definition",
                "mime": "text/markdown",
                "readable": True,
                "content_hash": content_hash,
            }
        if entry is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill resource not found")
        target = (root / path).resolve()
        resolved_root = root.resolve()
        if (target != resolved_root and resolved_root not in target.parents) or not target.is_file():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Skill resource not found")
        if raw:
            image_mimes = {
                ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
                ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
            }
            image_mime = image_mimes.get(target.suffix.lower())
            if image_mime is None:
                raise HTTPException(status_code=422, detail="Raw preview is only available for indexed image resources")
            if offset is not None or limit is not None or sheet is not None or char_offset:
                raise HTTPException(status_code=422, detail="Image previews do not accept pagination parameters")
            try:
                data = await asyncio.to_thread(target.read_bytes)
            except OSError as exc:
                raise HTTPException(status_code=404, detail="Skill resource not found") from exc
            return Response(
                content=data,
                media_type=image_mime,
                headers={
                    **preview_headers,
                    "Content-Security-Policy": "sandbox; default-src 'none'; img-src data:; style-src 'unsafe-inline'",
                },
            )
        try:
            page = await asyncio.to_thread(
                read_resource_page,
                entry,
                target,
                offset=offset,
                limit=limit,
                sheet=sheet,
                char_offset=char_offset,
                console_preview=True,
            )
        except SkillResourceReadError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc
        return SkillResourceContent(
            skill_code=code,
            path=path,
            format=page.format,
            unit=page.unit,
            content=page.content,
            metadata=page.metadata,
            offset=page.offset,
            char_offset=page.char_offset,
            next_offset=page.next_offset,
            next_char_offset=page.next_char_offset,
            has_more=page.has_more,
            returned_count=page.returned_count,
            total_count=page.total_count,
            truncated_by=page.truncated_by,
            content_hash=str(entry["content_hash"]),
        )

router.include_router(app_router)
