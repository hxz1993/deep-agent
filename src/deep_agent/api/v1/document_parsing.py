from __future__ import annotations

from pathlib import Path
import tempfile
import time

from fastapi import APIRouter, HTTPException
from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.core.context import AppContextDep
from deep_agent.diagnostics import emit
from deep_agent.documents.models import DocumentSource, DocumentParsingError
from deep_agent.documents.providers import PROVIDERS, parse_document
from deep_agent.documents.settings import (
    DOCUMENT_PARSING_SETTING_KEY, DOCUMENT_PARSING_SCHEMA_VERSION,
    DocumentParsingRuntimeConfig, DocumentParsingConfigurationError,
    read_document_parsing_config, resolve_document_parsing_config,
)
from deep_agent.schemas.llm_provider import mask_api_key
from deep_agent.schemas.document_parsing import (
    DocumentParsingSettingsResponse, DocumentParsingValueResponse,
    DocumentParsingSettingsWrite, DocumentParsingTestResponse,
)

router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)
tag = "document-parsing"


def _response(row, config):
    return DocumentParsingSettingsResponse(
        value=DocumentParsingValueResponse(mode=config.mode, provider=config.provider, model=config.model,
            api_key_set=bool(config.api_key), api_key_masked=mask_api_key(config.api_key), configuration_error=config.configuration_error),
        providers=[item.describe() for item in PROVIDERS.values()],
        created_at=getattr(row, "created_at", None), updated_at=getattr(row, "updated_at", None))


async def _current(ctx):
    store = ctx.stores.global_setting
    if store is None:
        raise HTTPException(503, "Global settings store is unavailable")
    row = await store.get(DOCUMENT_PARSING_SETTING_KEY)
    return row, read_document_parsing_config(row)


def _resolve(body, existing):
    try:
        return resolve_document_parsing_config(body.value.model_dump(exclude_none=True), existing=existing)
    except DocumentParsingConfigurationError as exc:
        raise HTTPException(422, str(exc)) from exc


def connection_sample() -> DocumentSource:
    """One deterministic PDF page, no business files or external fixture URL."""
    stream = b"BT /F1 20 Tf 72 720 Td (DeepAgent Document Test) Tj 0 -40 Td (Invoice amount: 123.45) Tj ET"
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
               b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
               b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"]
    data = bytearray(b"%PDF-1.4\n")
    positions = [0]
    for number, obj in enumerate(objects, 1):
        positions.append(len(data))
        data.extend(str(number).encode() + b" 0 obj\n" + obj + b"\nendobj\n")
    start = len(data)
    data.extend(b"xref\n0 6\n0000000000 65535 f \n")
    for position in positions[1:]:
        data.extend(f"{position:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{start}\n%%EOF\n".encode())
    return DocumentSource("document-connection-test.pdf", "application/pdf", bytes(data), 1)


@router.get("/global-settings/document-parsing", response_model=DocumentParsingSettingsResponse)
async def get_document_parsing_settings(ctx: AppContextDep):
    row, config = await _current(ctx)
    return _response(row, config)


@router.put("/global-settings/document-parsing", response_model=DocumentParsingSettingsResponse)
async def put_document_parsing_settings(body: DocumentParsingSettingsWrite, ctx: AppContextDep):
    _, existing = await _current(ctx)
    config = _resolve(body, existing)
    row = await ctx.stores.global_setting.upsert(DOCUMENT_PARSING_SETTING_KEY, value=config.to_stored_value(), version=DOCUMENT_PARSING_SCHEMA_VERSION)
    emit("document_configuration_updated", provider_name=config.provider or "unconfigured", mode=config.mode)
    return _response(row, config)


@router.post("/global-settings/document-parsing/test", response_model=DocumentParsingTestResponse)
async def test_document_parsing_settings(body: DocumentParsingSettingsWrite, ctx: AppContextDep):
    _, existing = await _current(ctx)
    config = _resolve(body, existing)
    started = time.monotonic()
    try:
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete-document-test-") as directory:
            result = await parse_document(config, connection_sample(), Path(directory))
        content = "\n".join(page.content for page in result.pages)
        if result.status != "complete" or "123.45" not in content or "DeepAgent" not in content:
            raise DocumentParsingError("sample_mismatch", "接口已返回，但测试页的已知内容未完整识别")
    except DocumentParsingError as exc:
        return DocumentParsingTestResponse(ok=False, provider=config.provider or "unconfigured", message=str(exc),
            error_code=exc.code, elapsed_ms=int((time.monotonic() - started) * 1000))
    return DocumentParsingTestResponse(ok=True, provider=config.provider or "unconfigured", message="连接成功，已解析并核对单页测试内容",
        elapsed_ms=int((time.monotonic() - started) * 1000))
