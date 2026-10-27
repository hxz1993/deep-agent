from __future__ import annotations

from datetime import datetime
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.agent.input_capabilities import upload_capabilities_payload
from deep_agent.core.context import AppContextDep
from deep_agent.documents.settings import load_document_parsing_config
from deep_agent.files.upload_settings import (
    UPLOAD_SETTING_KEY, UPLOAD_SCHEMA_VERSION, UploadSettingsError, read_upload_settings,
)

router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)
tag = "global-settings"


class UploadValue(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_size_mb: int = Field(strict=True, ge=1, le=200)
    max_count: int = Field(strict=True, ge=1, le=20)


class UploadSettingsWrite(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: UploadValue


class UploadSettingsResponse(BaseModel):
    version: int = UPLOAD_SCHEMA_VERSION
    value: dict[str, int] | None
    effective_value: UploadValue
    allowed_mimes: list[str]
    kinds: list[str]
    labels: dict[str, str]
    created_at: datetime | None = None
    updated_at: datetime | None = None


def _response(row, document):
    try:
        settings = read_upload_settings(row)
    except UploadSettingsError as exc:
        raise HTTPException(503, str(exc)) from exc
    capabilities = upload_capabilities_payload(document, upload_settings=settings)
    return UploadSettingsResponse(
        value=({name: row.value[name] for name in ("max_size_mb", "max_count") if name in row.value}
               if row is not None else None),
        effective_value=UploadValue(**settings.to_stored_value()),
        allowed_mimes=capabilities["allowed_mimes"], kinds=capabilities["kinds"], labels=capabilities["labels"],
        created_at=getattr(row, "created_at", None), updated_at=getattr(row, "updated_at", None),
    )


def _store(ctx):
    store = ctx.stores.global_setting
    if store is None:
        raise HTTPException(503, "附件上传配置存储不可用")
    return store


@router.get("/global-settings/attachment-upload", response_model=UploadSettingsResponse)
async def get_attachment_upload_settings(ctx: AppContextDep):
    row = await _store(ctx).get(UPLOAD_SETTING_KEY)
    document = await load_document_parsing_config(_store(ctx))
    return _response(row, document)


@router.put("/global-settings/attachment-upload", response_model=UploadSettingsResponse)
async def put_attachment_upload_settings(body: UploadSettingsWrite, ctx: AppContextDep):
    store = _store(ctx)
    # Resolve ancillary display data before committing. No fallible DB reads
    # after the write may turn an accepted save into an apparent rejection.
    document = await load_document_parsing_config(store)
    row = await store.upsert(UPLOAD_SETTING_KEY, value=body.value.model_dump(), version=UPLOAD_SCHEMA_VERSION)
    return _response(row, document)
