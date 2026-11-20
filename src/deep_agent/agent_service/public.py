"""Shared HTTP and MCP message schemas and task projection."""
from __future__ import annotations

import base64
from typing import Annotated, Literal

from a2a import types as wire
from fastapi import HTTPException
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from google.protobuf.struct_pb2 import Value
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from .execution import MAX_INPUT_BYTES
from .service import AgentService

TaskId = Annotated[str, StringConstraints(min_length=1, max_length=128)]
TaskState = Literal["submitted", "working", "input_required", "auth_required",
                    "completed", "failed", "canceled", "rejected"]
HistoryLength = Annotated[int, Field(ge=0, le=50)]


class PublicInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Attachment(PublicInput):
    name: str = Field(min_length=1, max_length=255)
    media_type: str = Field(min_length=1, max_length=255)
    data_base64: str = Field(min_length=1, max_length=(MAX_INPUT_BYTES + 2) // 3 * 4)


class MessageInput(PublicInput):
    message_id: TaskId
    text: str | None = Field(default=None, min_length=1, max_length=65536)
    data: dict | None = None
    attachments: list[Attachment] = Field(default_factory=list, max_length=20)

    def to_wire(self, **bindings) -> wire.Message:
        parts = []
        if self.text is not None:
            parts.append(wire.Part(text=self.text))
        if self.data is not None:
            try:
                parts.append(wire.Part(data=ParseDict(self.data, Value())))
            except (ParseError, ValueError, RecursionError, OverflowError) as exc:
                raise HTTPException(422, "消息的 JSON 数据无效") from exc
        total = 0
        for item in self.attachments:
            try:
                raw = base64.b64decode(item.data_base64, validate=True)
            except ValueError as exc:
                raise HTTPException(422, "附件须使用有效的 Base64 编码") from exc
            total += len(raw)
            if total > MAX_INPUT_BYTES:
                raise HTTPException(413, "附件总量超过 8 MiB")
            parts.append(wire.Part(raw=raw, filename=item.name, media_type=item.media_type))
        return wire.Message(role=wire.Role.ROLE_USER, message_id=self.message_id, parts=parts, **bindings)


class SubmitInput(MessageInput):
    context_id: TaskId | None = None
    reference_task_ids: list[TaskId] = Field(default_factory=list, max_length=10)


class ReplyInput(MessageInput):
    reply_to: TaskId | None = None


class TaskQuery(PublicInput):
    context_id: TaskId | None = None
    state: TaskState | None = None
    page_size: int = Field(default=20, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=4096)


def task_error(exc: Exception) -> HTTPException:
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, wire.TaskNotFoundError):
        return HTTPException(404, "任务不存在或已过期")
    if isinstance(exc, wire.ContentTypeNotSupportedError):
        return HTTPException(422, exc.message)
    if isinstance(exc, (wire.InvalidParamsError, wire.UnsupportedOperationError, wire.TaskNotCancelableError)):
        return HTTPException(409 if not isinstance(exc, wire.InvalidParamsError) else 400, exc.message)
    raise exc


def artifact_path(task_id: str, artifact_id: str) -> str:
    return f"/api/v1/agent-service/tasks/{task_id}/artifacts/{artifact_id}/download"


def artifact_reference(task_id: str, artifact_id, file, download_base_url: str | None = None) -> dict:
    reference = {
        "artifact_id": artifact_id, "name": file.name, "media_type": file.mime, "size": file.size,
    }
    if download_base_url:
        reference["download_url"] = download_base_url + artifact_path(task_id, artifact_id)
    return reference


def snapshot(entry, task=None, *, history_length=None, download_base_url: str | None = None) -> dict:
    task = entry.snapshot(history_length) if task is None else task
    artifacts = []
    for item in task.artifacts:
        if file := entry.files.get(item.artifact_id):
            artifacts.append(artifact_reference(task.id, item.artifact_id, file, download_base_url))
        else:
            artifacts.append({
                "artifact_id": item.artifact_id, "name": item.name,
                "parts": [MessageToDict(part, preserving_proto_field_name=True) for part in item.parts],
            })
    return {
        "id": task.id, "context_id": task.context_id,
        "state": wire.TaskState.Name(task.status.state).removeprefix("TASK_STATE_").lower(),
        "source": entry.source,
        "question_id": task.status.message.message_id if task.status.state == wire.TaskState.TASK_STATE_INPUT_REQUIRED else None,
        "message": "\n".join(part.text for part in task.status.message.parts if part.HasField("text")),
        "artifacts": artifacts,
        "history": [MessageToDict(message, preserving_proto_field_name=True) for message in task.history],
    }


class PublicTasks:
    def __init__(self, ctx, headers: dict[str, str], source: str, *,
                 client: str | None = None, download_base_url: str | None = None):
        self.service = AgentService(ctx)
        self.headers = headers
        self.source = source
        self.client = client
        self.download_base_url = download_base_url

    def _snapshot(self, entry, task=None, *, history_length=None) -> dict:
        return snapshot(entry, task, history_length=history_length, download_base_url=self.download_base_url)

    async def list_agents(self) -> dict:
        return {"agents": [
            {"agent_code": row.code, "name": row.name, "description": row.description}
            for row in await self.service.publications.list_enabled()
        ]}

    async def submit(self, agent_code: str, body: SubmitInput) -> dict:
        row = await self.service.publication(agent_code)
        owner = self.service.owner(row, self.headers)
        entry = await self.service.submit(
            row.code, owner, self.headers, body.to_wire(
                context_id=body.context_id or "", reference_task_ids=body.reference_task_ids,
            ), source=self.source, client=self.client,
        )
        return self._snapshot(entry)

    async def get(self, task_id: str, history_length: int | None = None) -> dict:
        _, entry = await self.service.find_task(task_id, self.headers)
        return self._snapshot(entry, history_length=history_length)

    async def list_tasks(self, agent_code: str, query: TaskQuery) -> dict:
        row = await self.service.publication(agent_code)
        owner = self.service.owner(row, self.headers)
        page = self.service.tasks.page(owner, wire.ListTasksRequest(
            context_id=query.context_id or "", page_size=query.page_size,
            page_token=query.cursor or "", include_artifacts=True, history_length=0,
            status=wire.TaskState.Value(f"TASK_STATE_{query.state.upper()}") if query.state else 0,
        ))
        return {
            "items": [self._snapshot(self.service.tasks.entries[task.id], task) for task in page.tasks],
            "next_cursor": page.next_page_token or None, "total": page.total_size,
        }

    async def reply(self, task_id: str, body: ReplyInput) -> dict:
        row, entry = await self.service.find_task(task_id, self.headers)
        message = body.to_wire(task_id=task_id)
        if body.reply_to:
            message.metadata.fields["replyTo"].string_value = body.reply_to
        entry = await self.service.submit(
            row.code, entry.context.owner, self.headers, message, source=self.source,
        )
        return self._snapshot(entry)

    async def cancel(self, task_id: str) -> dict:
        _, entry = await self.service.find_task(task_id, self.headers)
        return self._snapshot(entry, await self.service.tasks.cancel(entry))

    async def artifact(self, task_id: str, artifact_id: str):
        _, entry = await self.service.find_task(task_id, self.headers)
        return entry, self.service.artifact(entry, artifact_id)
