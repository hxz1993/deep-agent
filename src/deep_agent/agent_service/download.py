from __future__ import annotations

from time import monotonic
from urllib.parse import quote

import anyio
from fastapi.responses import StreamingResponse

from deep_agent.files.storage import FileStoreError, FileStoreIntegrityError


async def artifact_chunks(entry, artifact, handle, *, protocol):
    """Read only the declared bytes, diagnosing FileStore errors at the read boundary."""
    started = monotonic()
    remaining = artifact.size
    while True:
        try:
            chunk = await anyio.to_thread.run_sync(handle.read, min(64 * 1024, remaining + 1))
            remaining -= len(chunk)
            if remaining < 0 or (not chunk and remaining):
                raise FileStoreIntegrityError("文件大小与产物元数据不一致")
        except Exception as exc:
            entry.diagnose(phase="artifact_read", status="failed", protocol=protocol,
                           duration_ms=round((monotonic() - started) * 1000),
                           error_type=type(exc).__name__, reason="artifact_read_failed")
            if isinstance(exc, FileStoreError):
                raise
            raise FileStoreError("文件暂不可读取") from exc
        if not chunk:
            return
        yield chunk


class ArtifactDownloadResponse(StreamingResponse):
    def __init__(self, entry, artifact, handle, *, protocol):
        self.handle = handle
        super().__init__(
            artifact_chunks(entry, artifact, handle, protocol=protocol),
            media_type=artifact.mime,
            headers={
                "Content-Disposition": "attachment; filename*=UTF-8''" + quote(artifact.name, safe=""),
                "Content-Length": str(artifact.size),
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    async def __call__(self, scope, receive, send):
        with self.handle:
            await super().__call__(scope, receive, send)
