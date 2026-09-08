from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request, status
from starlette.responses import Response

from deep_agent.agent.headers import extract_forward_headers
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.files.storage import (
    LOCAL_FILE_PREFIX,
    FileStoreError,
    LocalFileStore,
)
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT


router = APIRouter()


@router.get("/local-files/{file_id}/download", include_in_schema=False)
async def download_local_file(
    file_id: str,
    request: Request,
    ctx: AppContextDep,
    user_id: RequestUserDep,
) -> Response:
    """Download a user-owned local-development file.

    The route intentionally disappears behind a 404 when the Platform backend
    is active.  It never proxies Platform files and never exposes system Skill
    packages.
    """

    if not isinstance(ctx.file_store, LocalFileStore) or not file_id.startswith(
        LOCAL_FILE_PREFIX
    ):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    headers = extract_forward_headers(request)
    headers["user"] = user_id
    try:
        metadata = await ctx.file_store.describe(file_id, headers)
        if metadata.file_type != FILE_TYPE_ATTACHMENT:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="File not found",
            )
        data = await ctx.file_store.download(file_id, headers)
    except HTTPException:
        raise
    except FileStoreError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="File not found",
        ) from exc
    return Response(
        content=data,
        media_type=metadata.mime or "application/octet-stream",
        headers={
            "Content-Disposition": (
                "attachment; filename*=UTF-8''" + quote(metadata.file_name)
            ),
            "X-Content-Type-Options": "nosniff",
        },
    )
