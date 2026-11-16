from __future__ import annotations

import asyncio
import hashlib
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from deep_agent.files.storage import FileStore
from deep_agent.files.upload_settings import load_upload_settings
from deep_agent.schemas.attachment import AttachmentRef
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT

from .contracts import (
    CONFIG_ADAPTER, CONNECTION_PREFIX, A2AConfig, Adapter, InputFile, McpConfig, Operation,
    RemoteAgentError, RemoteResult, RemoteState, RemoteTaskExpired,
)
from .journal import Invocation, InvocationJournal

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 32 * 1024 * 1024
MAX_TEXT = 128 * 1024


@dataclass(frozen=True, slots=True)
class RequestScope:
    user: str
    file_store: FileStore
    headers: Mapping[str, str] = field(repr=False)
    agent_id: str | None = None
    session_id: str | None = None
    app_id: str | None = None
    space_id: str | None = None
    turn_id: str | None = None
    run_id: str | None = None
    attachments: Sequence[AttachmentRef] = ()
    user_message: str | None = None
    remote_task_id: str | None = None


class RecentInvocations:
    def __init__(self, *, capacity: int = 30, retention: timedelta = timedelta(hours=24)):
        self.capacity = capacity
        self.retention = retention
        self.entries: OrderedDict[str, Invocation] = OrderedDict()

    def _prune(self):
        minimum = datetime.now(UTC) - self.retention
        for key, entry in list(self.entries.items()):
            if entry.created_at < minimum:
                del self.entries[key]
        while len(self.entries) > self.capacity:
            oldest = min(self.entries, key=lambda key: self.entries[key].created_at)
            del self.entries[oldest]

    def record(self, entry: Invocation):
        self.entries[entry.id] = entry.model_copy(deep=True)
        self._prune()

    def list(self, request: RequestScope) -> list[dict]:
        self._prune()
        visible = (entry for entry in self.entries.values() if (
            entry.user, entry.app_id, entry.space_id
        ) == (request.user, request.app_id, request.space_id))
        return [entry.summary() for entry in sorted(visible, key=lambda entry: entry.created_at, reverse=True)]


class RemoteAgentRuntime:
    def __init__(self, ctx, *, workspace_root: Path):
        self.ctx = ctx
        self.connections = ctx.stores.remote_agent
        self.journal = InvocationJournal(ctx.db_session_factory)
        self.workspace_root = workspace_root
        self.recent = RecentInvocations()
        self.debug_entries: OrderedDict[str, Invocation] = OrderedDict()
        self.operations: dict[str, asyncio.Task] = {}
        self.harness_sessions = {}

    async def adapter(self, connection) -> Adapter:
        config = CONFIG_ADAPTER.validate_python(connection.config)
        if isinstance(config, A2AConfig):
            from .network import A2AAdapter
            return A2AAdapter(config)
        if isinstance(config, McpConfig):
            return await self.mcp_adapter(config)
        from .cli import CliAdapter
        return CliAdapter(config, self.workspace_root)

    async def mcp_adapter(self, config: McpConfig):
        from .network import McpAdapter
        row = await self.ctx.stores.mcp.get_by_name(config.mcp_server_name)
        if row is None or row.transport != "url" or not row.config.get("url"):
            raise RemoteAgentError("MCP 连接已变更或不可用")
        return McpAdapter(config, str(row.config["url"]), row.config.get("headers") or {})

    async def authorize(self, entry: Invocation, request: RequestScope):
        if (entry.session_id, entry.agent_id, entry.user, entry.app_id, entry.space_id) != (
            request.session_id, request.agent_id, request.user, request.app_id, request.space_id,
        ):
            raise RemoteAgentError("当前会话无法访问该远程调用")
        connection = await self.connections.get(entry.connection_id)
        if connection is None or connection.revision != entry.connection_revision:
            raise RemoteAgentError("远程连接配置已变更，请发起新任务")
        if request.agent_id:
            agent = await self.ctx.stores.agent.get_by_id(request.agent_id)
            if agent is None or CONNECTION_PREFIX + connection.id not in (agent.capabilities or []):
                raise RemoteAgentError("当前 Agent 无权使用此远程连接")
        return connection

    def create(self, connection, request: RequestScope, *, invocation_id: str | None = None) -> Invocation:
        return Invocation(
            id=invocation_id or uuid4().hex, connection_id=connection.id,
            connection_revision=connection.revision, name=connection.name, protocol=connection.config["protocol"],
            user=request.user, session_id=request.session_id, agent_id=request.agent_id,
            app_id=request.app_id, space_id=request.space_id, turn_id=request.turn_id, run_id=request.run_id,
        )

    async def _save(self, entry: Invocation):
        entry.updated_at = datetime.now(UTC)
        if not entry.timeline or entry.timeline[-1]["status"] != entry.state.status:
            entry.timeline = [*entry.timeline[-23:], {"status": entry.state.status, "at": entry.updated_at.isoformat()}]
        if entry.session_id:
            await self.journal.save(entry)
            self.recent.record(entry)
        else:
            self.debug_entries[entry.id] = entry
            self.debug_entries.move_to_end(entry.id)
            for key in list(self.debug_entries):
                if key not in self.operations and (len(self.debug_entries) > 64 or
                        self.debug_entries[key].created_at < datetime.now(UTC) - timedelta(hours=24)):
                    del self.debug_entries[key]

    async def _inputs(self, request: RequestScope) -> list[InputFile]:
        files = []
        remaining = MAX_INPUT_BYTES
        settings = await load_upload_settings(self.ctx.stores.global_setting)
        if len(request.attachments) > settings.max_count:
            raise RemoteAgentError("附件数量超过当前限制")
        for attachment in request.attachments:
            try:
                data = await request.file_store.download_bounded(
                    attachment.id, request.headers, max_bytes=min(remaining, settings.max_bytes),
                )
            except Exception as error:
                raise RemoteAgentError("无法读取附件，或附件超过远程调用的大小限制") from error
            remaining -= len(data)
            files.append(InputFile(attachment.name or "attachment", attachment.mime or "application/octet-stream", data))
        return files

    async def _complete(self, entry: Invocation, request: RequestScope, result: RemoteResult):
        entry.state = result.state
        if result.text or entry.state.status == "input_required":
            entry.text = result.text[:MAX_TEXT]
        warnings = list(result.warnings)
        remaining = MAX_OUTPUT_BYTES
        settings = await load_upload_settings(self.ctx.stores.global_setting)
        for output in result.files:
            if output.key in entry.files:
                continue
            if len(entry.files) >= settings.max_count or len(output.data) > min(remaining, settings.max_bytes):
                warnings.append("部分产物超过文件大小或数量限制")
                continue
            try:
                stored = await request.file_store.upload(file_type=FILE_TYPE_ATTACHMENT,
                    file_name=output.name, mime=output.mime, data=output.data, request_headers=request.headers)
            except Exception:
                warnings.append("部分远程产物上传失败，可重新获取")
                continue
            remaining -= len(output.data)
            entry.files[output.key] = {"kind": "remote_agent_output", "file_id": stored.id,
                "file_name": stored.file_name, "mime": output.mime, "mime_type": output.mime,
                "byte_size": len(output.data), "sha256": hashlib.sha256(output.data).hexdigest()}
            await self._save(entry)
        entry.warnings = tuple(dict.fromkeys(warnings))
        await self._save(entry)
        return entry

    async def execute(self, entry: Invocation, request: RequestScope, message: str, mode: Operation) -> Invocation:
        connection = await self.authorize(entry, request)
        if mode == "resume" and request.run_id and entry.run_id == request.run_id and entry.state.status == "completed":
            return entry
        adapter = await self.adapter(connection)
        if mode == "answer" and entry.state.status != "input_required":
            raise RemoteAgentError("此任务当前没有待回答的问题")
        if mode == "resume" and entry.state.status != "completed":
            raise RemoteAgentError("远程任务尚未完成，不能开启后续任务")
        files = await self._inputs(request)
        previous = entry.state.model_copy(deep=True)
        entry.message_id = uuid4().hex
        if not entry.title:
            entry.title = " ".join((request.user_message or message).split())[:120]
        entry.turn_id, entry.run_id = request.turn_id, request.run_id
        entry.state = previous.model_copy(update={"status": "submitted"})
        entry.text, entry.warnings = "", ()
        await self._save(entry)

        async def checkpoint(state: RemoteState):
            if entry.state != state:
                entry.state = state.model_copy(deep=True)
                await self._save(entry)

        try:
            result = await adapter.run(previous, message, files, mode, checkpoint, message_id=entry.message_id)
        except RemoteAgentError as error:
            result = RemoteResult(entry.state.model_copy(update={"status": error.status}), str(error))
        except TimeoutError:
            result = RemoteResult(entry.state.model_copy(update={"status": "unknown"}), "远程调用超时，请查询原任务状态")
        return await self._complete(entry, request, result)

    async def refresh(self, entry: Invocation, request: RequestScope):
        connection = await self.authorize(entry, request)
        adapter = await self.adapter(connection)

        async def checkpoint(state):
            entry.state = state
            await self._save(entry)

        try:
            result = await adapter.refresh(entry.state, checkpoint)
        except RemoteTaskExpired as error:
            result = RemoteResult(entry.state.model_copy(update={"status": "failed", "question_id": None}), str(error))
        return await self._complete(entry, request, result)

    async def cancel(self, entry: Invocation, request: RequestScope):
        connection = await self.authorize(entry, request)
        adapter = await self.adapter(connection)
        return await self._complete(entry, request, await adapter.cancel(entry.state))

    async def close(self):
        for session in list(self.harness_sessions.values()):
            await session.close()
        operations = list(self.operations.values())
        for operation in operations:
            operation.cancel()
        await asyncio.gather(*operations, return_exceptions=True)
