"""MCP tools that use the published-Agent A2A task and message model."""
from __future__ import annotations

import base64
from typing import Annotated, Literal

from a2a import types as wire
from a2a.server.routes.common import serialize_list_tasks_response
from fastapi import HTTPException
from google.protobuf.json_format import MessageToDict, ParseDict, ParseError
from pydantic import Field, JsonValue

from .execution import MAX_INPUT_BYTES
from .public import HistoryLength, PublicInput, PublicTasks, TaskId, artifact_path
from .publications import AgentCode

DEFAULT_WAIT_SECONDS = 120
WaitSeconds = Annotated[float, Field(
    ge=0, allow_inf_nan=False, description="Maximum seconds to wait for the task. Use 0 for an immediate snapshot.",
)]


class MessagePart(PublicInput):
    text: str | None = None
    data: JsonValue = None
    raw: str | None = Field(default=None, max_length=(MAX_INPUT_BYTES + 2) // 3 * 4)
    filename: str | None = Field(default=None, max_length=255)
    mediaType: str | None = Field(default=None, max_length=255)


class Message(PublicInput):
    messageId: TaskId
    role: Literal["ROLE_USER"]
    parts: list[MessagePart] = Field(min_length=1, max_length=20)
    taskId: TaskId | None = None
    contextId: TaskId | None = None
    referenceTaskIds: list[TaskId] = Field(default_factory=list, max_length=10)
    metadata: dict[str, JsonValue] | None = None

    def to_wire(self) -> wire.Message:
        message = wire.Message()
        try:
            data = self.model_dump(exclude_unset=True)
            total = 0
            for part in data["parts"]:
                kinds = {"text", "data", "raw"}.intersection(part)
                if len(kinds) != 1 or any(part[k] is None for k in kinds if k != "data"):
                    raise ValueError("A message part must contain one content value")
                if "raw" in part:
                    total += len(base64.b64decode(part["raw"], validate=True))
                    if total > MAX_INPUT_BYTES:
                        raise HTTPException(413, "附件总量超过 8 MiB")
            # A2A's JSON encoding uses protobuf's native field and enum mapping.
            return ParseDict(data, message)
        except (ParseError, TypeError, ValueError, RecursionError, OverflowError) as exc:
            raise HTTPException(422, "消息的内容块无效") from exc


class SendConfiguration(PublicInput):
    historyLength: HistoryLength | None = None
    waitSeconds: WaitSeconds = DEFAULT_WAIT_SECONDS


class SendTool(PublicInput):
    agentCode: AgentCode | None = None
    message: Message
    configuration: SendConfiguration | None = None


class GetTool(PublicInput):
    id: TaskId
    historyLength: HistoryLength | None = None
    waitSeconds: WaitSeconds = DEFAULT_WAIT_SECONDS


class CancelTool(PublicInput):
    id: TaskId


class ListTool(PublicInput):
    agentCode: AgentCode
    contextId: TaskId | None = None
    status: Literal[
        "TASK_STATE_SUBMITTED", "TASK_STATE_WORKING", "TASK_STATE_INPUT_REQUIRED", "TASK_STATE_AUTH_REQUIRED",
        "TASK_STATE_COMPLETED", "TASK_STATE_FAILED", "TASK_STATE_CANCELED", "TASK_STATE_REJECTED",
    ] | None = None
    pageSize: int | None = Field(default=None, ge=1, le=100)
    pageToken: str | None = Field(default=None, max_length=4096)
    historyLength: HistoryLength | None = None
    statusTimestampAfter: str | None = Field(default=None, max_length=35, json_schema_extra={"format": "date-time"})
    includeArtifacts: bool = False

    def to_wire(self) -> wire.ListTasksRequest:
        try:
            return ParseDict(self.model_dump(exclude_none=True, exclude={"agentCode"}), wire.ListTasksRequest())
        except ParseError as exc:
            raise HTTPException(422, "查询时间须使用带时区的 RFC 3339 时间戳") from exc


class McpTasks(PublicTasks):
    def __init__(self, ctx, headers, *, download_base_url=None):
        super().__init__(ctx, headers, "mcp", download_base_url=download_base_url)

    def _address_task(self, entry, task: wire.Task) -> wire.Task:
        if self.download_base_url:
            for artifact in task.artifacts:
                if artifact.artifact_id in entry.files:
                    artifact.parts[0].url = self.download_base_url + artifact_path(task.id, artifact.artifact_id)
        return task

    def _snapshot(self, entry, task=None, *, history_length=None) -> dict:
        if task is None:
            task = entry.snapshot(history_length)
        return MessageToDict(self._address_task(entry, task))

    async def send(self, args: SendTool) -> dict:
        message = args.message.to_wire()
        if message.task_id:
            row, prior = await self.service.find_task(message.task_id, self.headers)
            if args.agentCode is not None and args.agentCode != row.code:
                raise HTTPException(400, "agentCode 与原任务的发布不一致")
            owner = prior.context.owner
        else:
            if args.agentCode is None:
                raise HTTPException(400, "新任务需要指定 agentCode")
            row = await self.service.publication(args.agentCode)
            owner = self.service.owner(row, self.headers)
        entry = await self.service.submit(row.code, owner, self.headers, message, source="mcp")
        configuration = args.configuration or SendConfiguration()
        task = await self.service.tasks.wait(
            entry, timeout=configuration.waitSeconds, history_length=configuration.historyLength,
        )
        return self._snapshot(entry, task)

    async def get_task(self, args: GetTool) -> dict:
        _, entry = await self.service.find_task(args.id, self.headers)
        task = await self.service.tasks.wait(entry, timeout=args.waitSeconds, history_length=args.historyLength)
        return self._snapshot(entry, task)

    async def page(self, args: ListTool) -> dict:
        row = await self.service.publication(args.agentCode)
        owner = self.service.owner(row, self.headers)
        page = self.service.tasks.page(owner, args.to_wire())
        for task in page.tasks:
            self._address_task(self.service.tasks.entries[task.id], task)
        return serialize_list_tasks_response(page, args.includeArtifacts)
