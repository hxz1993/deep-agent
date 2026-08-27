from __future__ import annotations

import asyncio
import hashlib
import io
import json
import shutil
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status
from loguru import logger
from starlette.responses import Response

from deep_agent.agent.headers import extract_forward_headers
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.core.context import AppContextDep
from deep_agent.db.models import SkillPackage
from deep_agent.schemas.skill import (
    SkillDetail,
    SkillInstallResult,
    SkillPackageRecord,
    SkillResource,
    SkillResourceContent,
    SkillReloadResult,
    SkillSummary,
    SkillUpdate,
)
from deep_agent.skills.catalog import SkillDescriptor, get_builtin_skill, list_builtin_skills
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
            SkillResource.model_validate(present_resource_entry(entry))
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
        code=row.code,
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
            SkillResource.model_validate(present_resource_entry(entry))
            for entry in index
        ],
    )


def _summary(detail: SkillDetail) -> SkillSummary:
    return SkillSummary.model_validate(detail.model_dump(exclude={"manifest", "resources"}))


async def _row_or_404(ctx: AppContextDep, code: str) -> SkillPackage:
    row = await ctx.stores.skill_package.get_by_code(code)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Skill not found: {code}")
    return row


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
    except Exception as exc:
        logger.warning(
            "Orphan platform Skill file cleanup failed context={} file_id={} error_type={}",
            context,
            file_id,
            type(exc).__name__,
        )


@router.get("/skills", response_model=list[SkillSummary])
async def list_skills(ctx: AppContextDep) -> list[SkillSummary]:
    details = [_builtin_detail(item) for item in list_builtin_skills()]
    details.extend(
        _row_detail(row, ctx.skill_runtime)
        for row in await ctx.stores.skill_package.list_all()
    )
    return [_summary(item) for item in sorted(details, key=lambda value: (value.source != "builtin", value.label, value.code))]


@router.get("/skills/export")
async def export_uploaded_skills(request: Request, ctx: AppContextDep) -> Response:
    rows = sorted(await ctx.stores.skill_package.list_all(), key=lambda row: row.code)
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
        "schema_version": "1.0",
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

    filename = f"deepagent-skills-export-{exported_at.strftime('%Y%m%dT%H%M%SZ')}.zip"
    return Response(
        content=output.getvalue(),
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/skills/{code}", response_model=SkillDetail)
async def get_skill(code: str, ctx: AppContextDep) -> SkillDetail:
    builtin = get_builtin_skill(code)
    if builtin is not None:
        return _builtin_detail(builtin)
    return _row_detail(await _row_or_404(ctx, code), ctx.skill_runtime)


@router.post("/skills", response_model=SkillInstallResult, status_code=status.HTTP_201_CREATED)
async def install_skill(
    request: Request,
    ctx: AppContextDep,
    file: UploadFile = File(...),
) -> SkillInstallResult:
    raw, filename = await _read_zip(file)
    parsed: ParsedSkillPackage | None = None
    try:
        parsed = parse_skill_zip(raw)
        if get_builtin_skill(parsed.code) is not None or await ctx.stores.skill_package.get_by_code(parsed.code):
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
                )
            )
        except Exception:
            await _best_effort_delete(
                ctx.file_store,
                file_headers,
                uploaded.id,
                context="install-compensation",
            )
            raise
        await ctx.skill_runtime.materialize(
            row,
            request_headers=request_headers,
            payload=raw,
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


@router.put("/skills/{code}/package", response_model=SkillInstallResult)
async def replace_skill_package(
    code: str,
    request: Request,
    ctx: AppContextDep,
    file: UploadFile = File(...),
) -> SkillInstallResult:
    _reject_builtin(code)
    current = await _row_or_404(ctx, code)
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
            except Exception as exc:  # noqa: BLE001 - explicit replacement repairs a missing source file
                logger.warning(
                    "Skill same-hash replacement will repair platform source "
                    "code={} error_type={}",
                    code,
                    type(exc).__name__,
                )
            else:
                await ctx.skill_runtime.materialize(
                    current,
                    request_headers=request_headers,
                    payload=raw,
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
            row = await ctx.stores.skill_package.replace_package(code, replacement)
            if row is None:
                raise RuntimeError(f"Skill disappeared during package update: {code}")
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
        await ctx.skill_runtime.materialize(
            row,
            request_headers=request_headers,
            payload=raw,
            force=True,
        )
        return SkillInstallResult(
            status="repaired" if same_content else "updated",
            skill=_row_detail(row, ctx.skill_runtime),
        )
    except SkillPackageError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    finally:
        if parsed is not None:
            shutil.rmtree(parsed.package_root.parent, ignore_errors=True)


@router.patch("/skills/{code}", response_model=SkillDetail)
async def update_skill(
    code: str,
    body: SkillUpdate,
    request: Request,
    ctx: AppContextDep,
) -> SkillDetail:
    _reject_builtin(code)
    row = await ctx.stores.skill_package.update(code, body)
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Skill not found: {code}")
    return _row_detail(row, ctx.skill_runtime)


@router.delete("/skills/{code}")
async def delete_skill(code: str, request: Request, ctx: AppContextDep) -> dict:
    _reject_builtin(code)
    row = await _row_or_404(ctx, code)
    agents = await ctx.stores.agent.list_referencing_skill(code)
    if agents:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "message": "Skill is still referenced by Agents",
                "agents": [item.name for item in agents],
            },
        )
    await ctx.stores.skill_package.delete(code)
    await ctx.skill_runtime.remove(code)
    file_headers = skill_platform_file_headers(extract_forward_headers(request))
    await _best_effort_delete(
        ctx.file_store,
        file_headers,
        row.platform_file_id,
        context="delete",
    )
    return {"status": "deleted", "code": code}


@router.post("/skills/{code}/reload", response_model=SkillReloadResult)
async def reload_skill(code: str, request: Request, ctx: AppContextDep) -> SkillReloadResult:
    _reject_builtin(code)
    row = await _row_or_404(ctx, code)
    request_headers = extract_forward_headers(request)
    runtime_state = await ctx.skill_runtime.materialize(
        row,
        request_headers=request_headers,
        force=True,
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


@router.get("/skills/{code}/resources", response_model=list[SkillResource])
async def list_skill_resources(code: str, ctx: AppContextDep) -> list[SkillResource]:
    detail = await get_skill(code, ctx)
    return detail.resources


@router.get("/skills/{code}/package")
async def download_skill_package(code: str, request: Request, ctx: AppContextDep) -> Response:
    _reject_builtin(code)
    row = await _row_or_404(ctx, code)
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
    return Response(
        content=raw,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(row.file_name)}"},
    )


async def _resource_root(code: str, request: Request, ctx: AppContextDep) -> tuple[Path, list[dict]]:
    builtin = get_builtin_skill(code)
    if builtin is not None:
        return builtin.package_root, builtin.resource_index
    row = await _row_or_404(ctx, code)
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
        runtime_state = ctx.skill_runtime.state(code)
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail={
                "message": "Skill runtime files are unavailable",
                "error_code": runtime_state.error_code or runtime_state.status,
            },
        )
    return resolved.package_root, resolved.resource_index


@router.get("/skills/{code}/resources/{path:path}", response_model=SkillResourceContent)
async def read_skill_resource(
    code: str,
    path: str,
    request: Request,
    ctx: AppContextDep,
    offset: int | None = None,
    limit: int | None = None,
    sheet: str | None = None,
) -> SkillResourceContent:
    root, index = await _resource_root(code, request, ctx)
    entry = next((item for item in index if item.get("path") == path and item.get("category") != "script"), None)
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
    try:
        page = await asyncio.to_thread(
            read_resource_page,
            entry,
            target,
            offset=offset,
            limit=limit,
            sheet=sheet,
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
        next_offset=page.next_offset,
        has_more=page.has_more,
        returned_count=page.returned_count,
        total_count=page.total_count,
        truncated_by=page.truncated_by,
        content_hash=str(entry["content_hash"]),
    )
