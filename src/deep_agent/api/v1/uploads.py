from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status

from deep_agent.agent.headers import extract_forward_headers
from deep_agent.agent.input_capabilities import upload_capabilities_payload, document_upload_mimes
from deep_agent.agent.media_pipeline import MediaPipelineError, process_upload
from deep_agent.api.v1.channel_auth import ChannelAccountDep
from deep_agent.core.context import AppContextDep
from deep_agent.documents.settings import load_document_parsing_config
from deep_agent.schemas.attachment import UploadResponse
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT

router = APIRouter()


@router.get("/uploads/capabilities")
async def list_upload_capabilities(ctx: AppContextDep) -> dict:
    """MIME types and upload kinds enabled by the platform upload pipeline."""
    config = await load_document_parsing_config(getattr(ctx.stores, "global_setting", None))
    return upload_capabilities_payload(config)


@router.post("/uploads", response_model=UploadResponse)
async def upload_file(
    request: Request,
    ctx: AppContextDep,
    channel_account: ChannelAccountDep,
    file: UploadFile = File(...),
) -> UploadResponse:
    raw = await file.read()
    filename = file.filename or "upload.bin"
    mime = file.content_type or "application/octet-stream"
    config = await load_document_parsing_config(getattr(ctx.stores, "global_setting", None))
    try:
        processed = process_upload(data=raw, mime=mime, filename=filename, additional_mimes=document_upload_mimes(config))
    except MediaPipelineError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    request_headers = extract_forward_headers(request, channel_account=channel_account)
    if channel_account is not None:
        request_headers["user"] = channel_account.session_user_id
    try:
        uploaded = await ctx.file_store.upload(
            file_type=FILE_TYPE_ATTACHMENT,
            file_name=processed.filename,
            mime=processed.mime,
            data=processed.data,
            request_headers=request_headers,
        )
    except Exception as exc:  # noqa: BLE001 - surface platform errors to client
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"File upload failed: {exc}",
        ) from exc

    return UploadResponse(
        id=uploaded.id,
        name=uploaded.file_name,
        mime=processed.mime,
        size=uploaded.file_size,
        extracted_text=None,
    )
