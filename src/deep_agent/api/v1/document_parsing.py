from __future__ import annotations

from pathlib import Path
import io
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
    # The previous 635-byte hand-written PDF was rejected by GLM-OCR even
    # though normal PDFs worked. Use a normal rendered page plus a text layer,
    # generated with existing dependencies, for a representative OCR probe.
    from PIL import Image, ImageDraw, ImageFont
    from pypdf import PdfWriter
    from pypdf.generic import DictionaryObject, NameObject, NumberObject, DecodedStreamObject, EncodedStreamObject

    image = Image.new("RGB", (1224, 1584), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=40)
    draw.text((144, 105), "DeepAgent Document Test", fill="black", font=font)
    draw.text((144, 185), "Invoice amount: 123.45", fill="black", font=font)
    pixels = io.BytesIO()
    image.save(pixels, format="JPEG", quality=95)
    writer = PdfWriter()
    page = writer.add_blank_page(width=612, height=792)
    raster = EncodedStreamObject()
    raster._data = pixels.getvalue()
    raster.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"),
                   NameObject("/Width"): NumberObject(1224), NameObject("/Height"): NumberObject(1584),
                   NameObject("/ColorSpace"): NameObject("/DeviceRGB"), NameObject("/BitsPerComponent"): NumberObject(8),
                   NameObject("/Filter"): NameObject("/DCTDecode")})
    text_font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
                                  NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({
        NameObject("/XObject"): DictionaryObject({NameObject("/Sample"): writer._add_object(raster)}),
        NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(text_font)}),
    })
    content = DecodedStreamObject()
    content.set_data(b"q 612 0 0 792 0 0 cm /Sample Do Q\n"
                     b"BT /F1 20 Tf 3 Tr 72 720 Td (DeepAgent Document Test) Tj 0 -40 Td (Invoice amount: 123.45) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(content)
    output = io.BytesIO()
    writer.write(output)
    return DocumentSource("document-connection-test.pdf", "application/pdf", output.getvalue(), 1)


@router.get("/global-settings/document-parsing", response_model=DocumentParsingSettingsResponse)
async def get_document_parsing_settings(ctx: AppContextDep):
    row, config = await _current(ctx)
    return _response(row, config)


@router.put("/global-settings/document-parsing", response_model=DocumentParsingSettingsResponse)
async def put_document_parsing_settings(body: DocumentParsingSettingsWrite, ctx: AppContextDep):
    store = ctx.stores.global_setting
    if store is None:
        raise HTTPException(503, "Global settings store is unavailable")
    row = await store.update_atomic(
        DOCUMENT_PARSING_SETTING_KEY,
        transform=lambda current: _resolve(body, read_document_parsing_config(current)).to_stored_value(),
        version=DOCUMENT_PARSING_SCHEMA_VERSION,
    )
    config = read_document_parsing_config(row)
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
