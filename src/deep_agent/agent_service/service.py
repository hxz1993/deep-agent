"""Task operations shared by the published Agent transports."""
from __future__ import annotations

from time import monotonic

from a2a import types as wire
from fastapi import HTTPException, Request

from deep_agent.files.storage import FileStoreError, FileStoreNotFoundError, FileStorePermissionError

from .output import PublicationProtocol
from .execution import MAX_INPUT_BYTES, RunnerExecution, prepare_input
from .publications import PublicationScope, PublicationStore
from .tasks import Owner, TaskStore

MAX_BODY_BYTES = MAX_INPUT_BYTES * 4 // 3 + 256 * 1024


async def bounded_body(request: Request) -> bytes:
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > MAX_BODY_BYTES:
            raise HTTPException(413, "请求超过体积上限")
        body.extend(chunk)
    return bytes(body)


def task_store(ctx) -> TaskStore:
    if ctx.agent_tasks is None:
        ctx.agent_tasks = TaskStore()
    return ctx.agent_tasks


class AgentService:
    def __init__(self, ctx):
        self.ctx = ctx
        self.tasks = task_store(ctx)
        self.publications = PublicationStore(ctx.db_session_factory)

    async def publication(self, code: str):
        row = await self.publications.get(code)
        if row is None:
            raise HTTPException(404, "发布不存在")
        return row

    @staticmethod
    def owner(row, headers: dict[str, str]) -> Owner:
        app = headers.get("app")
        if not app:
            raise HTTPException(400, "请在 app Header 中传入 App ID")
        space = headers["space"]
        if not PublicationScope.model_validate(row.scope).allows(app, space):
            raise HTTPException(403, "指定的应用不在该发布的允许范围内，请检查发布配置和 App ID。")
        return Owner(row.id, headers["user"], app, space)

    async def find_task(self, task_id: str, headers: dict[str, str]):
        if not headers.get("app"):
            raise HTTPException(400, "请在 app Header 中传入 App ID")
        self.tasks.prune()
        entry = self.tasks.entries.get(task_id)
        if entry is None:
            raise wire.TaskNotFoundError()
        owner = entry.context.owner
        if (owner.user, owner.app, owner.space) != (headers["user"], headers["app"], headers["space"]):
            raise wire.TaskNotFoundError()
        row = await self.publications.get_by_id(owner.publication_id)
        if row is None:
            raise wire.TaskNotFoundError()
        return row, self.tasks.get(self.owner(row, headers), task_id)

    async def submit(
        self, code: str, owner: Owner, headers: dict[str, str], message: wire.Message, *,
        source: PublicationProtocol = "a2a", client: str | None = None, executor=RunnerExecution,
    ):
        text, uploads = await prepare_input(self.ctx, message)
        async with self.tasks.admission:
            row = await self.publications.get(code)
            if row is None or row.id != owner.publication_id:
                raise wire.TaskNotFoundError()
            self.owner(row, headers)
            return self.tasks.submit(
                owner, row.agent_id, message, enabled=row.enabled,
                execute=executor(self.ctx, headers, code, text, uploads, source),
                input_size=len(text.encode()) + sum(len(upload.data) for upload in uploads),
                source=source, client=client,
            )

    @staticmethod
    def artifact(entry, artifact_id):
        artifact = entry.files.get(artifact_id)
        if artifact is None:
            raise HTTPException(404, "文件不存在或已过期")
        return artifact

    async def open_artifact(self, entry, artifact, headers, *, protocol):
        started = monotonic()
        try:
            return await self.ctx.file_store.open_download(artifact.id, headers, expected_size=artifact.size)
        except FileStoreError as exc:
            unavailable = isinstance(exc, (FileStorePermissionError, FileStoreNotFoundError))
            entry.diagnose(phase="artifact_read", status="failed", protocol=protocol,
                           duration_ms=round((monotonic() - started) * 1000), error_type=type(exc).__name__,
                           reason="artifact_unavailable" if unavailable else "artifact_read_failed")
            if unavailable:
                raise HTTPException(404, "文件不存在或已过期") from exc
            raise HTTPException(502, "文件暂不可读取") from exc
