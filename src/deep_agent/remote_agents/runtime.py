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
    CONFIG_ADAPTER, CONNECTION_PREFIX, REMOTE_AGENT_PROTOCOLS, A2AConfig, Adapter, InputFile, Operation,
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
    tool_call_id: str | None = None
    attachments: Sequence[AttachmentRef] = ()
    user_message: str | None = None
    remote_task_id: str | None = None
    remote_question_id: str | None = None
    allowed_connections: frozenset[str] | None = None

    @classmethod
    def from_deps(cls, deps, allowed_connections: frozenset[str] | None = None):
        return cls(
            deps.user, deps.file_store, deps.execution_request_headers or deps.request_headers,
            agent_id=deps.agent_id, session_id=deps.session_id, app_id=deps.app_id, space_id=deps.space_id,
            turn_id=deps.turn_id, run_id=deps.run_id,
            user_message=deps.remote_user_message, remote_task_id=deps.remote_task_id,
            remote_question_id=deps.remote_question_id, allowed_connections=allowed_connections,
        )


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
        if entry.protocol not in REMOTE_AGENT_PROTOCOLS:
            return
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
        self.debug_actions: set[str] = set()
        self.harness_sessions = {}

    async def adapter(self, connection) -> Adapter:
        if connection.config.get("protocol") not in REMOTE_AGENT_PROTOCOLS:
            raise RemoteAgentError("此远程 Agent 接入方式已不可用")
        config = CONFIG_ADAPTER.validate_python(connection.config)
        if isinstance(config, A2AConfig):
            from .network import A2AAdapter
            return A2AAdapter(config)
        from .cli import CliAdapter
        return CliAdapter(config, self.workspace_root)

    async def authorize(self, entry: Invocation, request: RequestScope):
        if (entry.session_id, entry.agent_id, entry.user, entry.app_id, entry.space_id) != (
            request.session_id, request.agent_id, request.user, request.app_id, request.space_id,
        ):
            raise RemoteAgentError("当前会话无法访问该远程调用")
        if request.allowed_connections is not None and entry.connection_id not in request.allowed_connections:
            raise RemoteAgentError("本轮未启用此远程连接")
        connection = await self.connections.get(entry.connection_id)
        if connection is None or connection.revision != entry.connection_revision:
            raise RemoteAgentError("远程连接配置已变更，请发起新任务")
        if connection.config.get("protocol") not in REMOTE_AGENT_PROTOCOLS:
            raise RemoteAgentError("此远程 Agent 接入方式已不可用")
        if request.agent_id:
            agent = await self.ctx.stores.agent.get_by_id(request.agent_id)
            if agent is None or CONNECTION_PREFIX + connection.id not in (agent.capabilities or []):
                raise RemoteAgentError("当前 Agent 无权使用此远程连接")
        return connection

    async def validate_reply(self, request: RequestScope) -> None:
        entries = await self.journal.list(request.session_id, user=request.user, agent_id=request.agent_id,
                                          app_id=request.app_id, space_id=request.space_id)
        entry = next((entry for entry in entries if entry.id == request.remote_task_id), None)
        if entry is None or entry.state.status != "input_required":
            raise RemoteAgentError("所选远程任务在当前会话中没有待回答的问题")
        await self.authorize(entry, request)
        if request.remote_question_id is not None and request.remote_question_id != entry.state.question_id:
            raise RemoteAgentError("远程补问已更新，请刷新后重新选择问题")

    def create(self, connection, request: RequestScope, *, invocation_id: str | None = None) -> Invocation:
        return Invocation(
            id=invocation_id or uuid4().hex, connection_id=connection.id,
            connection_revision=connection.revision, name=connection.name, protocol=connection.config["protocol"],
            user=request.user, session_id=request.session_id, agent_id=request.agent_id,
            app_id=request.app_id, space_id=request.space_id, turn_id=request.turn_id, run_id=request.run_id,
        )

    async def _save(self, entry: Invocation, **changes):
        snapshot = entry.model_copy(update={**changes, "updated_at": datetime.now(UTC)})
        if not snapshot.timeline or snapshot.timeline[-1]["status"] != snapshot.state.status:
            snapshot.timeline = [*snapshot.timeline[-23:], {"status": snapshot.state.status, "at": snapshot.updated_at.isoformat()}]
        if snapshot.session_id:
            await self.journal.save(snapshot)
            self.recent.record(snapshot)
        # Do not publish uncommitted changes to the user or the running Harness.
        entry.__dict__.update(snapshot.__dict__)
        if not entry.session_id:
            self.debug_entries[entry.id] = entry
            self.debug_entries.move_to_end(entry.id)
            for key in list(self.debug_entries):
                if key not in self.operations and key not in self.debug_actions and (len(self.debug_entries) > 64 or
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
        state = result.state
        text = result.text[:MAX_TEXT] if result.text or state.status == "input_required" else entry.text
        warnings = list(result.warnings)
        remaining = MAX_OUTPUT_BYTES - sum(file.get("byte_size", 0) for file in entry.files.values())
        settings = await load_upload_settings(self.ctx.stores.global_setting) if result.files else None
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
            file = {"kind": "remote_agent_output", "file_id": stored.id,
                "file_name": stored.file_name, "mime": output.mime, "mime_type": output.mime,
                "byte_size": len(output.data), "sha256": hashlib.sha256(output.data).hexdigest()}
            await self._save(
                entry, files={**entry.files, output.key: file},
                file_message_ids={**entry.file_message_ids, output.key: entry.message_id or ""},
            )
        notices = tuple(dict.fromkeys(warnings))
        if (state, text, notices) != (entry.state, entry.text, entry.warnings):
            await self._save(entry, state=state, text=text, warnings=notices)
        return entry

    async def execute(self, entry: Invocation, request: RequestScope, message: str, mode: Operation,
                      *, files: Sequence[InputFile] | None = None) -> Invocation:
        connection = await self.authorize(entry, request)
        if mode == "resume" and request.run_id and entry.run_id == request.run_id and entry.state.status == "completed":
            return entry
        adapter = await self.adapter(connection)
        if mode == "answer" and entry.state.status != "input_required":
            raise RemoteAgentError("此任务当前没有待回答的问题")
        if mode == "answer" and request.remote_question_id and entry.state.question_id != request.remote_question_id:
            raise RemoteAgentError("远程补问已更新，请刷新后重新选择问题")
        if mode == "resume" and entry.state.status != "completed":
            raise RemoteAgentError("远程任务尚未完成，不能开启后续任务")
        if files is None:
            files = await self._inputs(request)
        previous = entry.state.model_copy(deep=True)
        message_id = uuid4().hex
        title = entry.title or " ".join((request.user_message or message).split())[:120]
        started = {"status": "submitted", "question_id": None}
        if mode == "resume" and entry.protocol == "a2a":
            # A follow-up creates a different upstream task. Until its ID is
            # known, the previous completed task is not a recovery handle.
            started["task_id"] = None
        await self._save(entry, message_id=message_id, title=title, turn_id=request.turn_id,
                         tool_call_id=request.tool_call_id,
                         run_id=request.run_id, state=previous.model_copy(update=started), text="", warnings=())

        async def checkpoint(state: RemoteState):
            # Save the upstream recovery handle immediately. Publish completion
            # only after _complete has collected text, file references and warnings.
            if state.status == "completed":
                state = state.model_copy(update={"status": entry.state.status})
            if entry.state != state:
                await self._save(entry, state=state.model_copy(deep=True))

        try:
            result = await adapter.run(previous, message, files, mode, checkpoint, message_id=message_id)
        except RemoteAgentError as error:
            result = RemoteResult(entry.state.model_copy(update={"status": error.status}), str(error))
        except TimeoutError:
            result = RemoteResult(entry.state.model_copy(update={"status": "unknown"}), "远程调用超时，请查询原任务状态")
        return await self._complete(entry, request, result)

    async def execute_debug(self, entry: Invocation, request: RequestScope, message: str, mode: Operation):
        try:
            return await self.execute(entry, request, message, mode)
        except Exception as error:
            text = str(error) if isinstance(error, RemoteAgentError) else "调试执行未完成，请检查连接后重试"
            if entry.state.status in {"input_required", "completed"}:
                # Input validation can fail before a new step is sent. Keep
                # the current question/result so the user can fix the input.
                await self._save(entry, warnings=(text,))
            else:
                status = "unknown" if entry.state.task_id else "failed"
                await self._save(entry, state=entry.state.model_copy(update={"status": status}), text=text)
            return entry

    async def refresh(self, entry: Invocation, request: RequestScope):
        connection = await self.authorize(entry, request)
        adapter = await self.adapter(connection)

        async def checkpoint(state):
            if state.status == "completed":
                state = state.model_copy(update={"status": entry.state.status})
            if state != entry.state:
                await self._save(entry, state=state)

        try:
            result = await adapter.refresh(entry.state, checkpoint)
        except RemoteTaskExpired as error:
            result = RemoteResult(entry.state.model_copy(update={"status": "failed", "question_id": None}), str(error))
        return await self._complete(entry, request, result)

    async def cancel(self, entry: Invocation, request: RequestScope):
        connection = await self.authorize(entry, request)
        adapter = await self.adapter(connection)
        result = await adapter.cancel(entry.state)
        active = self.operations.get(entry.id)
        if active and result.state.status == "canceled":
            active.cancel()
            await asyncio.gather(active, return_exceptions=True)
        return await self._complete(entry, request, result)

    async def close(self):
        for session in list(self.harness_sessions.values()):
            await session.close()
        operations = list(self.operations.values())
        for operation in operations:
            operation.cancel()
        await asyncio.gather(*operations, return_exceptions=True)
