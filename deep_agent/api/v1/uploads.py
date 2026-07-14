from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status

from deep_agent.agent.headers import extract_forward_headers
from deep_agent.agent.input_capabilities import upload_capabilities_payload
from deep_agent.agent.media_pipeline import MediaPipelineError, process_upload
from deep_agent.schemas.attachment import UploadResponse
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT, PlatformAPI

router = APIRouter()


@router.get("/uploads/capabilities")
async def list_upload_capabilities() -> dict:
    """MIME types and upload kinds enabled by the platform upload pipeline."""
    return upload_capabilities_payload()


@router.post("/uploads", response_model=UploadResponse)
async def upload_file(request: Request, file: UploadFile = File(...)) -> UploadResponse:
    raw = await file.read()
    filename = file.filename or "upload.bin"
    mime = file.content_type or "application/octet-stream"
    try:
        processed = process_upload(data=raw, mime=mime, filename=filename)
    except MediaPipelineError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    api = PlatformAPI(header=extract_forward_headers(request))
    try:
        uploaded = await api.file.upload(
            file_type=FILE_TYPE_ATTACHMENT,
            file_name=processed.filename,
            file=processed.data,
        )
        print(f"file uploaded: {uploaded}")
    except Exception as exc:  # noqa: BLE001 - surface platform errors to client
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Platform upload failed: {exc}",
        ) from exc

    return UploadResponse(
        id=uploaded.id,
        name=uploaded.fileName,
        mime=processed.mime,
        size=uploaded.fileSize,
        extracted_text=processed.extracted_text,
    )
