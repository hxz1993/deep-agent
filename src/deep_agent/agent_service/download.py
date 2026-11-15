from __future__ import annotations

import asyncio
from urllib.parse import quote

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask


async def artifact_download(service, artifact, headers):
    response_headers = {
        "Content-Disposition": "attachment; filename*=UTF-8''" + quote(artifact.name, safe=""),
        "Content-Length": str(artifact.size),
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
    }
    handle = await service.open_artifact(artifact, headers)

    async def chunks():
        try:
            while chunk := await asyncio.to_thread(handle.read, 64 * 1024):
                yield chunk
        finally:
            handle.close()

    return StreamingResponse(
        chunks(), media_type=artifact.mime, headers=response_headers, background=BackgroundTask(handle.close),
    )
