from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile, status
from loguru import logger

from deep_agent.agent.headers import extract_forward_headers
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.core.context import AppContextDep
from deep_agent.schemas.configuration_import import (
    ConfigurationImportResponse,
    ConfigurationImportSource,
)
from deep_agent.services.configuration_import import (
    MAX_CONFIGURATION_BACKUP_BYTES,
    MAX_SKILLS_BUNDLE_BYTES,
    ConfigurationImportConflictError,
    ConfigurationImportError,
    ConfigurationImportFileStoreError,
    ConfigurationImportPlan,
    apply_configuration_import,
    build_configuration_import_plan,
)
from deep_agent.skills.runtime import RUNTIME_READY, skill_platform_file_headers


router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)
tag = "Configuration Import"
_UPLOAD_CHUNK_BYTES = 1024 * 1024


async def _close_uploads(*uploads: UploadFile | None) -> None:
    for upload in uploads:
        if upload is not None:
            await upload.close()


async def _write_upload(
    upload: UploadFile,
    path: Path,
    *,
    max_bytes: int,
) -> None:
    total = 0
    try:
        descriptor = os.open(
            path,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "wb") as destination:
            while chunk := await upload.read(_UPLOAD_CHUNK_BYTES):
                total += len(chunk)
                if total > max_bytes:
                    raise HTTPException(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail="Uploaded initialization file exceeds its size limit",
                    )
                destination.write(chunk)
        if total == 0:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="Uploaded initialization file is empty",
            )
    finally:
        await upload.close()


def _response(
    plan: ConfigurationImportPlan,
    *,
    imported: bool,
    skill_files_uploaded: int = 0,
    warnings: list[str] | None = None,
) -> ConfigurationImportResponse:
    return ConfigurationImportResponse(
        status="imported" if imported else "validated",
        source=ConfigurationImportSource(
            environment=plan.source.environment,
            exported_at=plan.source.exported_at,
            source_dialect=plan.source.source_dialect,
            revision=plan.source.revision,
            git_commit=plan.source.git_commit,
        ),
        target_revision=plan.target_revision,
        row_counts=dict(plan.row_counts),
        agent_ids=list(plan.agent_ids),
        skill_packages=len(plan.skills),
        skill_files_uploaded=skill_files_uploaded,
        restart_recommended=bool(imported and warnings),
        warnings=list(warnings or ()),
    )


@router.post(
    "/configuration/import",
    response_model=ConfigurationImportResponse,
)
async def import_configuration(
    request: Request,
    ctx: AppContextDep,
    database_file: UploadFile = File(...),
    skills_bundle: UploadFile | None = File(default=None),
    apply: bool = Form(default=False),
    confirm: bool = Form(default=False),
) -> ConfigurationImportResponse:
    if apply and not confirm:
        await _close_uploads(database_file, skills_bundle)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Formal configuration import requires confirm=true",
        )
    database_name = (database_file.filename or "").strip().lower()
    if not database_name.endswith(".db"):
        await _close_uploads(database_file, skills_bundle)
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="database_file must be a DeepAgent configuration backup .db file",
        )
    if skills_bundle is not None:
        skills_name = (skills_bundle.filename or "").strip().lower()
        if not skills_name.endswith(".zip"):
            await _close_uploads(database_file, skills_bundle)
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="skills_bundle must be a DeepAgent Skills export .zip file",
            )

    with tempfile.TemporaryDirectory(
        prefix="deep-agent-configuration-import-TMP_to_delete-"
    ) as tmpdir:
        root = Path(tmpdir)
        if os.name == "posix":
            os.chmod(root, 0o700)
        database_path = root / "configuration.db"
        skills_path = root / "skills.zip" if skills_bundle is not None else None
        try:
            await _write_upload(
                database_file,
                database_path,
                max_bytes=MAX_CONFIGURATION_BACKUP_BYTES,
            )
            if skills_bundle is not None and skills_path is not None:
                await _write_upload(
                    skills_bundle,
                    skills_path,
                    max_bytes=MAX_SKILLS_BUNDLE_BYTES,
                )
        except Exception:
            await _close_uploads(database_file, skills_bundle)
            raise

        try:
            plan = await build_configuration_import_plan(
                database_path,
                target_engine=ctx.db_engine,
                skills_bundle_path=skills_path,
                active_run_count=ctx.active_runs.active_count(),
            )
            if not apply:
                return _response(plan, imported=False)

            request_headers = skill_platform_file_headers(
                extract_forward_headers(request)
            )
            result = await apply_configuration_import(
                plan,
                target_engine=ctx.db_engine,
                file_store=ctx.file_store,
                request_headers=request_headers,
                active_run_count=ctx.active_runs.active_count(),
            )
            warnings: list[str] = []
            if result.plan.skills:
                try:
                    await ctx.skill_runtime.reconcile(ctx.stores)
                    for payload in result.plan.skills:
                        row = await ctx.stores.skill_package.get_by_code(payload.code)
                        if (
                            row is None
                            or ctx.skill_runtime.state_for(row).status != RUNTIME_READY
                        ):
                            warnings.append(
                                "Skill runtime requires retry after restart: "
                                f"{payload.code}"
                            )
                except Exception as exc:
                    logger.warning(
                        "Configuration import runtime reconciliation failed "
                        "error_type={}",
                        type(exc).__name__,
                    )
                    for payload in result.plan.skills:
                        warnings.append(
                            f"Skill runtime requires retry after restart: {payload.code}"
                        )
            logger.info(
                "Configuration import completed source_environment={} revision={} "
                "tables={} agents={} skills={}",
                result.plan.source.environment,
                result.plan.source.revision,
                len(result.plan.row_counts),
                len(result.plan.agent_ids),
                result.skill_files_uploaded,
            )
            return _response(
                result.plan,
                imported=True,
                skill_files_uploaded=result.skill_files_uploaded,
                warnings=warnings,
            )
        except ConfigurationImportConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=str(exc),
            ) from exc
        except ConfigurationImportFileStoreError as exc:
            logger.warning(
                "Configuration import Skill upload failed error_type={}",
                type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_502_BAD_GATEWAY,
                detail=str(exc),
            ) from exc
        except ConfigurationImportError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=str(exc),
            ) from exc


__all__ = ["import_configuration", "router"]
