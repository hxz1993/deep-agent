from __future__ import annotations

import asyncio
import hashlib
import secrets
from collections import OrderedDict
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from a2a import types as wire
from google.protobuf.timestamp_pb2 import Timestamp

from deep_agent import diagnostics

SUBMITTED = wire.TaskState.TASK_STATE_SUBMITTED
WORKING = wire.TaskState.TASK_STATE_WORKING
COMPLETED = wire.TaskState.TASK_STATE_COMPLETED
FAILED = wire.TaskState.TASK_STATE_FAILED
CANCELED = wire.TaskState.TASK_STATE_CANCELED
REJECTED = wire.TaskState.TASK_STATE_REJECTED
INPUT_REQUIRED = wire.TaskState.TASK_STATE_INPUT_REQUIRED
AUTH_REQUIRED = wire.TaskState.TASK_STATE_AUTH_REQUIRED
TERMINAL = frozenset({COMPLETED, FAILED, CANCELED, REJECTED})
PAUSED = frozenset({INPUT_REQUIRED, AUTH_REQUIRED})
RETENTION = timedelta(hours=24)
MAX_HISTORY = 24
MAX_ACTIVE = 64
MAX_TERMINAL = 500
MAX_OBSERVERS = 16
MAX_MESSAGES = 256
MAX_HISTORY_BYTES = 128 * 1024
MAX_ARTIFACTS = 128
MAX_PENDING_INPUT_BYTES = 32 * 1024 * 1024


@dataclass(frozen=True)
class Owner:
    publication_id: str
    user: str
    app: str
    space: str


@dataclass(frozen=True)
class ArtifactFile:
    id: str
    name: str
    mime: str
    size: int


@dataclass
class Result:
    artifacts: list[wire.Artifact] = field(default_factory=list)
    files: dict[str, ArtifactFile] = field(default_factory=dict)
    question: str | None = None
    status_detail: str | None = None
    state: int = COMPLETED
    reason: str | None = None
    error_type: str | None = None


@dataclass
class Conversation:
    id: str
    session_id: str
    owner: Owner
    agent_id: str
    active_id: str | None = None
    source: str = "a2a"


@dataclass
class Entry:
    context: Conversation
    task: wire.Task
    title: str
    created: datetime
    changed: datetime
    source: str = "a2a"
    client: str | None = None
    segment_protocol: str | None = None
    segment_run_id: str | None = None
    run_ids: list[str] = field(default_factory=list)
    files: dict[str, ArtifactFile] = field(default_factory=dict)
    progress: list[tuple[int, datetime]] = field(default_factory=list)
    subscribers: set[asyncio.Queue] = field(default_factory=set, repr=False)
    worker: asyncio.Task | None = field(default=None, repr=False)
    stop: Callable[[], None] | None = field(default=None, repr=False)
    cancellation_requested: bool = False
    retry_input: tuple[str, list] | None = field(default=None, repr=False)

    @property
    def state(self) -> int:
        return self.task.status.state

    def snapshot(self, history_length: int | None = None, *, artifacts: bool = True) -> wire.Task:
        task = wire.Task()
        task.CopyFrom(self.task)
        if history_length is not None:
            del task.history[:max(0, len(task.history) - history_length)]
        if not artifacts:
            task.ClearField("artifacts")
        return task

    def diagnose(self, *, status: str, phase: str, duration_ms: int, protocol: str | None = None,
                 reason: str | None = None, error_type: str | None = None) -> None:
        diagnostics.emit(
            "published_agent_task", level="ERROR" if status == "failed" else "INFO",
            task_id=self.task.id, publication_id=self.context.owner.publication_id,
            session_id=self.context.session_id, agent_id=self.context.agent_id, run_id=self.segment_run_id,
            protocol=protocol or self.segment_protocol or self.source, phase=phase, status=status,
            duration_ms=duration_ms, artifact_count=len(self.files), reason=reason, error_type=error_type,
        )

    def status(self, state: int, message: str | None = None, *,
               detail: str | None = None, reason: str | None = None, error_type: str | None = None) -> None:
        previous = self.state
        self.changed = datetime.now(UTC)
        timestamp = Timestamp()
        timestamp.FromDatetime(self.changed)
        status = wire.TaskStatus(state=state, timestamp=timestamp)
        if message:
            status.message.CopyFrom(wire.Message(
                message_id=uuid4().hex, role=wire.Role.ROLE_AGENT,
                task_id=self.task.id, context_id=self.context.id, parts=[wire.Part(text=message)],
            ))
            if detail:
                status.message.parts.add(text=detail)
        self.task.status.CopyFrom(status)
        self.progress.append((state, self.changed))
        del self.progress[:-12]
        self.broadcast(wire.TaskStatusUpdateEvent(
            task_id=self.task.id, context_id=self.context.id, status=status,
        ))
        if state in TERMINAL:
            self.context.active_id = None
            self.retry_input = None
        if state != previous:
            self.diagnose(status=wire.TaskState.Name(state).removeprefix("TASK_STATE_").lower(),
                          phase="task", duration_ms=round((self.changed - self.created).total_seconds() * 1000),
                          reason=reason, error_type=error_type)

    def append_history(self, message: wire.Message) -> None:
        self.task.history.add().CopyFrom(message)
        total = 0
        for count, item in enumerate(reversed(self.task.history), start=1):
            total += item.ByteSize()
            if count > MAX_HISTORY or total > MAX_HISTORY_BYTES:
                del self.task.history[:len(self.task.history) - count + 1]
                break

    def broadcast(self, event) -> None:
        for queue in self.subscribers:
            if queue.full():
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(None)
            else:
                queue.put_nowait(event)


Execute = Callable[[Entry, wire.Message, bool], Awaitable[Result]]


class TaskStore:
    """The API event loop owns task state. Mutations have no suspension points."""

    def __init__(self):
        self.entries: dict[str, Entry] = {}
        self.contexts: dict[str, Conversation] = {}
        self.messages: dict[tuple[Owner, str], tuple[str, bytes]] = {}
        self.admission = asyncio.Lock()
        self._slots = asyncio.Semaphore(4)
        self._cursors: OrderedDict[str, tuple] = OrderedDict()
        self._closed = False
        self._pending_input_bytes = 0
        self._expiry: asyncio.TimerHandle | None = None

    def prune(self) -> None:
        now = datetime.now(UTC)
        for entry in self.entries.values():
            if entry.state in PAUSED and now - entry.changed >= RETENTION:
                entry.status(FAILED, "等待补充已超时", reason="input_expired")
        terminal = sorted((entry for entry in self.entries.values() if entry.state in TERMINAL),
                          key=lambda entry: entry.changed)
        remove = {entry.task.id for index, entry in enumerate(terminal)
                  if now - entry.changed >= RETENTION or index < len(terminal) - MAX_TERMINAL}
        if remove:
            for task_id in remove:
                del self.entries[task_id]
            live_contexts = {entry.context.id for entry in self.entries.values()}
            self.contexts = {key: value for key, value in self.contexts.items() if key in live_contexts}
            self.messages = {key: value for key, value in self.messages.items() if value[0] not in remove}

        if self._expiry is not None:
            self._expiry.cancel()
            self._expiry = None
        if not self._closed:
            due = min((entry.changed + RETENTION for entry in self.entries.values()
                       if entry.state in TERMINAL | PAUSED), default=None)
            if due is not None:
                self._expiry = asyncio.get_running_loop().call_later(
                    max(0, (due - now).total_seconds()), self.prune,
                )

    def get(self, owner: Owner, task_id: str) -> Entry:
        self.prune()
        entry = self.entries.get(task_id)
        if entry is None or entry.context.owner != owner:
            raise wire.TaskNotFoundError()
        return entry

    def submit(
        self, owner: Owner, agent_id: str, message: wire.Message, *,
        enabled: bool, execute: Execute, input_size: int = 0,
        source: str = "a2a", client: str | None = None,
    ) -> Entry:
        self.prune()
        if self._closed:
            raise wire.InternalError(message="服务正在停止")
        if (message.role != wire.Role.ROLE_USER or not message.message_id
                or len(message.message_id) > 128 or not message.parts or len(message.parts) > 20):
            raise wire.InvalidParamsError(message="需要有效的用户消息及内容")
        key = (owner, message.message_id)
        digest = hashlib.sha256(message.SerializeToString(deterministic=True)).digest()
        if previous := self.messages.get(key):
            if previous[1] != digest:
                raise wire.InvalidParamsError(message="messageId 已用于不同消息")
            return self.get(owner, previous[0])

        if self._pending_input_bytes + input_size > MAX_PENDING_INPUT_BYTES:
            raise wire.UnsupportedOperationError(message="待处理消息容量已满，请稍后重试")
        for reference in message.reference_task_ids:
            self.get(owner, reference)
        continuing = bool(message.task_id)
        if continuing:
            entry = self.get(owner, message.task_id)
            if message.context_id and message.context_id != entry.context.id:
                raise wire.InvalidParamsError(message="taskId 和 contextId 不匹配")
            if entry.state not in PAUSED:
                raise wire.UnsupportedOperationError(message="此任务当前不能补答")
            if sum(task_id == entry.task.id for task_id, _ in self.messages.values()) >= MAX_MESSAGES:
                raise wire.UnsupportedOperationError(message="此任务已达到补答次数上限")
        else:
            if not enabled:
                raise wire.UnsupportedOperationError(message="发布已停用")
            if sum(item.state not in TERMINAL for item in self.entries.values()) >= MAX_ACTIVE:
                raise wire.UnsupportedOperationError(message="任务队列已满，请稍后重试")
            if message.context_id:
                context = self.contexts.get(message.context_id)
                if context is None or context.owner != owner:
                    raise wire.InvalidParamsError(message="未知 contextId")
                if context.agent_id != agent_id or context.active_id:
                    raise wire.UnsupportedOperationError(message="当前 Context 有活动任务或执行范围已改变")
            else:
                context = Conversation(uuid4().hex, uuid4().hex, owner, agent_id, source=source)
            task = wire.Task(id=uuid4().hex, context_id=context.id)
            description = next((" ".join(part.text.split())[:240] for part in message.parts if part.text.strip()),
                               "已提交附件或结构化输入")
            title = description[:100]
            task.metadata.fields["title"].string_value = title
            task.metadata.fields["description"].string_value = description
            entry = Entry(context, task, title, datetime.now(UTC), datetime.now(UTC), source=source, client=client)
            self.contexts[context.id] = context
            self.entries[task.id] = entry
            context.active_id = task.id

        inbound = wire.Message()
        inbound.CopyFrom(message)
        inbound.context_id, inbound.task_id = entry.context.id, entry.task.id
        # A2A history is a bounded wire projection. Binary content is kept by FileStore.
        history = wire.Message()
        history.CopyFrom(inbound)
        history.ClearField("metadata")
        for part in history.parts:
            if part.WhichOneof("content") == "raw":
                part.text = "已提交附件"
        entry.append_history(history)
        self.messages[key] = entry.task.id, digest
        entry.segment_protocol = source
        entry.segment_run_id = None
        entry.status(SUBMITTED)
        self._pending_input_bytes += input_size
        worker = entry.worker = asyncio.create_task(self._run(entry, history, continuing, execute))

        def release_input(_):
            self._pending_input_bytes -= input_size
            if entry.worker is worker:
                entry.worker = None
            self.prune()

        worker.add_done_callback(release_input)
        return entry

    async def _run(self, entry: Entry, message: wire.Message, continuing: bool, execute: Execute):
        try:
            async with self._slots:
                entry.status(WORKING)
                async with asyncio.timeout(600):
                    result = await execute(entry, message, continuing)
                if len(entry.task.artifacts) + len(result.artifacts) > MAX_ARTIFACTS:
                    entry.status(FAILED, "任务产物超过交付上限", reason="artifact_limit")
                    return
                entry.files.update(result.files)
                for artifact in result.artifacts:
                    entry.task.artifacts.append(artifact)
                    entry.broadcast(wire.TaskArtifactUpdateEvent(
                        task_id=entry.task.id, context_id=entry.context.id, artifact=artifact, last_chunk=True,
                    ))
                entry.status(
                    result.state, result.question, detail=result.status_detail,
                    reason=result.reason, error_type=result.error_type,
                )
                if result.state in PAUSED and result.question:
                    entry.append_history(entry.task.status.message)
        except asyncio.CancelledError:
            if entry.state not in TERMINAL:
                entry.status(CANCELED if entry.cancellation_requested else FAILED, "执行已停止",
                             reason="execution_canceled", error_type="CancelledError")
            raise
        except TimeoutError:
            entry.status(FAILED, "执行超时", reason="execution_timeout", error_type="TimeoutError")
        except Exception as exc:
            entry.status(FAILED, "执行失败，请查看 DeepAgent 诊断",
                         reason="execution_failed", error_type=type(exc).__name__)
        finally:
            entry.worker = None
            entry.stop = None

    async def cancel(self, entry: Entry) -> wire.Task:
        if entry.state in TERMINAL:
            if entry.state == CANCELED:
                return entry.snapshot()
            raise wire.TaskNotCancelableError()
        worker = entry.worker
        entry.cancellation_requested = True
        if entry.stop:
            entry.stop()
        elif worker is not None and entry.state == SUBMITTED:
            worker.cancel()
            entry.status(CANCELED)
        elif worker is not None and entry.state == WORKING:
            # Authorization and Runner setup can be awaiting I/O before
            # execution registers its cooperative cancellation callback.
            worker.cancel()
        elif entry.state in PAUSED:
            entry.status(CANCELED)
        if worker is not None:
            try:
                async with asyncio.timeout(15):
                    await asyncio.shield(asyncio.gather(worker, return_exceptions=True))
            except TimeoutError as exc:
                raise wire.TaskNotCancelableError(message="尚未确认执行停止") from exc
        if entry.state != CANCELED:
            raise wire.TaskNotCancelableError()
        return entry.snapshot()

    async def observe(self, entry: Entry, *, new_send: bool = False) -> AsyncIterator:
        if not new_send and entry.state in TERMINAL:
            raise wire.UnsupportedOperationError(message="终态任务请通过 GetTask 查询")
        if len(entry.subscribers) >= MAX_OBSERVERS:
            raise wire.UnsupportedOperationError(message="订阅数已达上限")
        queue = asyncio.Queue(maxsize=32)
        entry.subscribers.add(queue)
        try:
            initial = entry.snapshot()
            yield initial
            if initial.status.state in TERMINAL | PAUSED:
                return
            while (event := await queue.get()) is not None:
                yield event
                if isinstance(event, wire.TaskStatusUpdateEvent) and event.status.state in TERMINAL | PAUSED:
                    return
            yield entry.snapshot()
        finally:
            entry.subscribers.discard(queue)

    async def wait(self, entry: Entry, *, timeout: float | None = None,
                   history_length: int | None = None) -> wire.Task:
        worker = entry.worker
        if worker is not None and timeout != 0:
            # The request observes the worker. Its timeout or cancellation does not stop the task.
            await asyncio.wait((worker,), timeout=timeout)
        return entry.snapshot(history_length)

    def page(self, owner: Owner, params: wire.ListTasksRequest) -> wire.ListTasksResponse:
        self.prune()
        size = params.page_size if params.HasField("page_size") else 50
        if not 1 <= size <= 100:
            raise wire.InvalidParamsError(message="pageSize 必须在 1 到 100 之间")
        cutoff = params.status_timestamp_after.ToNanoseconds() if params.HasField("status_timestamp_after") else None
        binding = (owner, params.context_id, params.status, cutoff, params.include_artifacts,
                   params.history_length if params.HasField("history_length") else None)
        rows = sorted((
            entry for entry in self.entries.values()
            if entry.context.owner == owner
            and (not params.context_id or entry.context.id == params.context_id)
            and (not params.status or entry.state == params.status)
            and (cutoff is None or entry.task.status.timestamp.ToNanoseconds() >= cutoff)
        ), key=lambda entry: (entry.changed, entry.task.id), reverse=True)
        total = len(rows)
        if params.page_token:
            cursor = self._cursors.get(params.page_token)
            if cursor is None or cursor[0] != binding or datetime.now(UTC) >= cursor[2]:
                raise wire.InvalidParamsError(message="分页游标无效或已失效")
            rows = [entry for entry in rows if (entry.changed, entry.task.id) < cursor[1]]
        token = ""
        if len(rows) > size:
            token = secrets.token_urlsafe(24)
            boundary = rows[size - 1]
            self._cursors[token] = binding, (boundary.changed, boundary.task.id), datetime.now(UTC) + timedelta(minutes=10)
            if len(self._cursors) > 512:
                self._cursors.popitem(last=False)
        return wire.ListTasksResponse(
            tasks=[entry.snapshot(binding[-1], artifacts=params.include_artifacts) for entry in rows[:size]],
            next_page_token=token, page_size=min(size, len(rows)), total_size=total,
        )

    async def close(self) -> None:
        self._closed = True
        if self._expiry is not None:
            self._expiry.cancel()
            self._expiry = None
        workers = [entry.worker for entry in self.entries.values() if entry.worker is not None]
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)
