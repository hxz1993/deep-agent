from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from contextvars import ContextVar
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime

from pydantic import TypeAdapter, ValidationError
from pydantic_ai import ModelRetry, RunContext, ToolReturn
from pydantic_ai.capabilities import AbstractCapability, on_event
from pydantic_ai.toolsets import FunctionToolset
from pydantic_ai_harness.subagents import (
    DelegationTask, DelegationTasks, InMemoryTransport, RemoteSubAgent, SubAgents, TaskAnsweredEvent,
    TaskCancelledEvent, TaskCancelRequestedEvent, TaskCompletedEvent, TaskControl, TaskFailedEvent,
    TaskForceCancelRequestedEvent, TaskQuestionAskedEvent, TaskRequestEvent, TaskResumedEvent,
    TaskStartedEvent, TaskSteeredEvent,
)

from deep_agent.agent.deps import AgentDeps
from deep_agent.schemas.attachment import AttachmentRef

from .contracts import InputFile, RemoteAgentError
from .runtime import RequestScope


_ATTACHMENT_TOOLS = frozenset({"delegate_task", "answer_task"})
_ATTACHMENT_REFS = TypeAdapter(list[AttachmentRef])
_CHAT_ATTACHMENT_FIELDS = AttachmentRef.model_json_schema()["properties"]


@dataclass
class RemoteStep:
    request: RequestScope
    files: list[InputFile] = field(repr=False)

    def take_files(self) -> list[InputFile]:
        files, self.files = self.files, []
        return files


def _target(connection_id: str) -> str:
    return "remote_" + connection_id.replace("-", "_")


class HarnessSession(InMemoryTransport):
    def __init__(self, runtime):
        super().__init__()
        self.runtime = runtime
        self.answers: dict[tuple[str, str], RemoteStep] = {}
        self.owner = DelegationTasks(max_depth=2)
        self.manager = self.owner.opened()
        self.workers: dict[str, asyncio.Task] = {}
        self.entries = {}
        self.advanced = asyncio.Condition()
        self.touched = datetime.now(UTC)
        self.closing = False
        self.active_runs = 0

    async def open(self):
        await self.manager.__aenter__()

    async def close(self):
        self.closing = True
        for worker in list(self.workers.values()):
            worker.cancel()
        await asyncio.gather(*self.workers.values(), return_exceptions=True)
        await self.manager.__aexit__(None, None, None)

    async def send(self, message):
        if isinstance(message, TaskRequestEvent):
            if message.task_id not in self.workers:
                step = SessionTransport.step.get()
                if step is None:
                    raise RemoteAgentError("缺少本次远程工具调用的信息")
                self.workers[message.task_id] = asyncio.create_task(self._work(message, step))
            return
        if isinstance(message, (TaskCancelRequestedEvent, TaskForceCancelRequestedEvent)):
            worker = self.workers.get(message.task_id)
            if worker is not None:
                worker.cancel()
            return
        if isinstance(message, TaskSteeredEvent):
            # HTTP/CLI adapters can receive more input at a provider step boundary.
            return
        await super().send(message)

    async def _work(self, event: TaskRequestEvent, step: RemoteStep):
        request = step.request
        identifiers = {"task_id": event.task_id, "agent_name": event.agent_name}
        entry = self.entries.get(event.task_id)
        try:
            await super().send(TaskStartedEvent(**identifiers))
            if isinstance(event, TaskResumedEvent) and entry is None:
                raise RemoteAgentError("远程任务已不存在")
            if entry is None:
                connection = await self.runtime.connections.get(event.agent_name.removeprefix("remote_").replace("_", "-"))
                if connection is None:
                    raise RemoteAgentError("远程 Agent 连接不存在")
                entry = self.runtime.create(connection, request, invocation_id=event.task_id)
                self.entries[event.task_id] = entry
                mode, prompt = "start", event.prompt
            elif entry.state.status == "input_required":
                if request.run_id == entry.run_id or not request.user_message:
                    raise RemoteAgentError("请让用户先回复远程 Agent 的补充问题")
                if request.remote_task_id and request.remote_task_id != entry.id:
                    raise RemoteAgentError("用户选择了其他远程任务")
                question_id = entry.state.question_id
                if request.remote_question_id not in (None, question_id):
                    raise RemoteAgentError("远程补问已更新，请刷新后重新选择问题")
                await self.runtime.refresh(entry, request)
                if entry.state.status != "input_required" or entry.state.question_id != question_id:
                    raise RemoteAgentError("远程补问已更新，请刷新后重新选择问题")
                request = replace(request, remote_question_id=question_id)
                mode, prompt = "answer", request.user_message
            else:
                mode, prompt = "resume", event.prompt
            while True:
                await self.runtime.execute(entry, request, prompt, mode, files=step.take_files())
                try:
                    async with asyncio.timeout(600):
                        while entry.state.status in ("submitted", "working") and entry.state.task_id:
                            await asyncio.sleep(1)
                            await self.runtime.refresh(entry, request)
                except TimeoutError:
                    await self.runtime._save(entry, state=entry.state.model_copy(update={"status": "unknown"}),
                                             text="远端任务尚未结束，可按原任务查询进度。")
                if entry.state.status != "input_required":
                    break
                question_id = entry.state.question_id
                if not question_id or not entry.text:
                    raise RemoteAgentError("远程 Agent 返回了不完整的补问")
                await super().send(TaskQuestionAskedEvent(**identifiers, question_id=question_id, question=entry.text))
                async with self.advanced:
                    self.advanced.notify_all()
                async with asyncio.timeout(24 * 60 * 60):
                    while True:
                        answer = await self.receive(f"task:{entry.id}:worker")
                        if isinstance(answer, TaskAnsweredEvent) and answer.question_id == question_id:
                            step = self.answers.pop((entry.id, question_id), None)
                            if step is not None:
                                request = step.request
                                prompt = request.user_message
                                break
                mode = "answer"
            if entry.state.status == "completed":
                output = json.dumps(entry.result_payload(), ensure_ascii=False)
                await super().send(TaskCompletedEvent(**identifiers, output=output))
            elif entry.state.status == "canceled":
                await super().send(TaskCancelledEvent(**identifiers, forced=False))
            else:
                await super().send(TaskFailedEvent(**identifiers, error=entry.text or "未能确认远程任务的结果"))
        except asyncio.CancelledError:
            if entry and not self.closing and (entry.state.task_id or entry.state.session_id):
                with suppress(Exception):
                    await self.runtime.cancel(entry, request)
            if entry and entry.state.status == "canceled":
                await super().send(TaskCancelledEvent(**identifiers, forced=True))
            else:
                await super().send(TaskFailedEvent(**identifiers, error="远程取消未确认，请查询原任务状态"))
        except RemoteAgentError as error:
            await super().send(TaskFailedEvent(**identifiers, error=str(error)))
        except Exception:
            await super().send(TaskFailedEvent(**identifiers, error="远程 Agent 调用未完成，请查询任务状态"))
        finally:
            self.workers.pop(event.task_id, None)
            for key in tuple(self.answers):
                if key[0] == event.task_id:
                    self.answers.pop(key, None)
            await self.release(f"task:{event.task_id}:worker")
            async with self.advanced:
                self.advanced.notify_all()


class SessionTransport(InMemoryTransport):
    current = ContextVar("deep_agent_remote_session", default=None)
    step: ContextVar[RemoteStep | None] = ContextVar("deep_agent_remote_step", default=None)

    async def send(self, message):
        await self.current.get().send(message)

    async def receive(self, mailbox):
        return await self.current.get().receive(mailbox)

    async def release(self, mailbox):
        await self.current.get().release(mailbox)


class RemoteLifecycle(AbstractCapability[AgentDeps]):
    def __init__(self, runtime, connections):
        self.runtime = runtime
        self.connection_ids = frozenset(row.id for row in connections)
        targets = {_target(row.id) for row in connections}
        self.target_aliases = {row.name: _target(row.id) for row in connections if row.name not in targets}
        self.task_for_call: dict[str, str] = {}
        self.toolset = FunctionToolset[AgentDeps]()
        self.toolset.add_function(self.remote_task_result)

    def get_toolset(self):
        return self.toolset

    def get_instructions(self):
        labels = json.dumps(list(self.target_aliases), ensure_ascii=False)
        return (
            f"When setting agent_name in delegate_task, use the exact configured display name from {labels} "
            "or an exact identifier in the SubAgents list. "
            "Remote agents can ask the user for additional information. Relay each question to the user and "
            "finish the current reply. Prefer foreground delegation when the user is waiting for a reply. "
            "Use attachments to explicitly select only the file IDs needed for each task, follow-up or answer. "
            "Omitting attachments sends no new files. Use IDs from this conversation, including its history. "
            "Do not invent answers. After the user replies, use answer_task for the chosen pending question. "
            "For a restored pending question use delegate_task with resume set to its original task ID. "
            "Use delegate_task with resume for a new question in a completed remote conversation. "
            "Present the result and its file references together. Use remote_task_result only to recover "
            "a missing result or retry retrieval of unavailable files."
        )

    async def wrap_run(self, ctx, *, handler):
        deps = ctx.deps
        if not deps.user or deps.file_store is None:
            return await handler()
        request = RequestScope.from_deps(deps, self.connection_ids)
        key = (deps.session_id, deps.user, deps.agent_id, deps.app_id, deps.space_id)
        session = self.runtime.harness_sessions.get(key)
        if session is None:
            await self._prune_sessions()
            session = HarnessSession(self.runtime)
            self.runtime.harness_sessions[key] = session
            await session.open()
        session.active_runs += 1
        try:
            session.owner.aliases.clear()
            session.owner.aliases.update(self.target_aliases)
            await self._restore(ctx.conversation_id, session, request)
            token = SessionTransport.current.set(session)
            try:
                with session.owner.bind():
                    self._describe_pending_reply(ctx, session, request)
                    return await handler()
            finally:
                SessionTransport.current.reset(token)
        finally:
            session.active_runs -= 1
            session.touched = datetime.now(UTC)

    async def _prune_sessions(self):
        sessions = self.runtime.harness_sessions
        for key, session in sorted(sessions.items(), key=lambda item: item[1].touched):
            if len(sessions) < 128:
                return
            if not session.workers and not session.active_runs and sessions.get(key) is session:
                del sessions[key]
                await session.close()
        if len(sessions) >= 128:
            raise RemoteAgentError("远程 Agent 并发会话已满")

    async def _restore(self, conversation_id, session, request):
        entries = await self.runtime.journal.list(
            request.session_id, user=request.user, agent_id=request.agent_id,
            app_id=request.app_id, space_id=request.space_id,
        )
        for entry in entries:
            if entry.id in session.workers or entry.connection_id not in self.connection_ids:
                continue
            session.entries[entry.id] = entry
            if entry.id in session.owner.records:
                continue
            await session.owner.restore(DelegationTask(
                id=entry.id, agent_name=_target(entry.connection_id), conversation_id=conversation_id,
                prompt="", status="finished", outcome="ok", delivered=True,
                output=(f"Restored {entry.state.status} task {entry.id}. "
                        "Use resume to answer its pending question, or remote_task_result to check it."),
            ))

    def _describe_pending_reply(self, ctx, session, request):
        if not request.user_message:
            return
        pending = [
            {
                "task_id": entry.id, "agent_name": entry.name, "question": entry.text,
                "tool": "answer_task" if entry.id in session.workers else "delegate_task with resume",
            }
            for entry in session.entries.values()
            if entry.state.status == "input_required" and entry.run_id != request.run_id
            and request.remote_task_id in (None, entry.id)
            and request.remote_question_id in (None, entry.state.question_id)
        ]
        if pending:
            encoded = json.dumps(pending, ensure_ascii=False).replace("<", "\\u003c")
            ctx.enqueue(f"<pending_remote_questions>{encoded}</pending_remote_questions>")

    @on_event(TaskAnsweredEvent)
    async def validate_answer(self, ctx, event):
        session = SessionTransport.current.get()
        entry = session.entries.get(event.task_id) if session is not None else None
        step = SessionTransport.step.get()
        if entry is None or step is None:
            event.veto("No pending remote task in this conversation.")
            return
        request = step.request
        key = (event.task_id, event.question_id)
        if entry.run_id == request.run_id or not request.user_message:
            event.veto("Wait for the user to answer this question in the next turn.")
        elif request.remote_task_id not in (None, event.task_id):
            event.veto("The user chose a different task for this reply.")
        elif entry.state.question_id != event.question_id or request.remote_question_id not in (None, event.question_id):
            event.veto("The chosen remote question is no longer pending.")
        else:
            try:
                await self.runtime.authorize(entry, request)
            except RemoteAgentError as error:
                event.veto(str(error))
                return
            if key in session.answers or session.owner.pending_question(entry.id) is None:
                event.veto("This question already has an answer.")
                return
            session.answers[key] = RemoteStep(replace(request, remote_question_id=event.question_id), step.take_files())
            self.task_for_call[request.tool_call_id] = entry.id

    @on_event(TaskSteeredEvent)
    async def unsupported_steering(self, ctx, event):
        event.veto("This agent accepts replies to its questions, or a follow-up after its current work ends.")

    @on_event(TaskRequestEvent)
    async def track_delegation(self, ctx, event):
        if ctx.tool_call_id:
            self.task_for_call[ctx.tool_call_id] = event.task_id

    async def prepare_tools(self, ctx, tool_defs):
        return [
            replace(tool, parameters_json_schema={
                **tool.parameters_json_schema,
                "properties": {
                    **tool.parameters_json_schema["properties"],
                    "attachments": {
                        "type": "array",
                        "description": "Select file IDs for this request only. Omit to send no new files.",
                        "items": {
                            "type": "object",
                            "properties": {key: _CHAT_ATTACHMENT_FIELDS[key] for key in ("id", "name", "mime")},
                            "required": ["id"],
                        },
                    },
                },
            }) if tool.name in _ATTACHMENT_TOOLS else tool
            for tool in tool_defs
        ]

    async def before_tool_validate(self, ctx, *, call, tool_def, args):
        if call.tool_name not in _ATTACHMENT_TOOLS:
            return args
        try:
            selected = call.args_as_dict()
        except ValueError:
            return args
        if "attachments" in selected:
            return {key: value for key, value in selected.items() if key != "attachments"}
        return args

    async def _selected_files(self, ctx, session, request, call):
        try:
            requested = _ATTACHMENT_REFS.validate_python(call.args_as_dict().get("attachments", []))
        except ValidationError as error:
            raise ModelRetry("attachments must be an array of file references with an id.") from error
        attachments = []
        for file_id in dict.fromkeys(ref.id for ref in requested):
            if ref := ctx.deps.attachment_by_id.get(file_id):
                attachments.append(ref)
                continue
            artifact = ctx.deps.session_file_artifacts.get(file_id)
            if artifact is None:
                artifact = next((
                    artifact for entry in session.entries.values() for artifact in entry.files.values()
                    if artifact["file_id"] == file_id
                ), None)
            if artifact is None or artifact.get("kind") == "tool_result_overflow":
                raise ModelRetry(f"File {file_id!r} is not a sendable file in this conversation.")
            attachments.append(AttachmentRef(
                id=file_id, name=artifact.get("file_name"), mime=artifact.get("mime_type") or artifact.get("mime"),
            ))
        try:
            return await self.runtime._inputs(replace(request, attachments=attachments)) if attachments else []
        except RemoteAgentError as error:
            raise ModelRetry(str(error)) from error

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        session = SessionTransport.current.get()
        if session is None or call.tool_name not in _ATTACHMENT_TOOLS:
            return await handler(args)
        request = replace(RequestScope.from_deps(ctx.deps, self.connection_ids), tool_call_id=call.tool_call_id)
        files = await self._selected_files(ctx, session, request, call)
        token = SessionTransport.step.set(RemoteStep(request, files))
        try:
            result = await handler(args)
            task_id = self.task_for_call.get(call.tool_call_id)
            entry = session.entries.get(task_id) if task_id else None
            if entry is None:
                return result
            if call.tool_name == "answer_task":
                with suppress(TimeoutError):
                    async with asyncio.timeout(75), session.advanced:
                        await session.advanced.wait_for(lambda: entry.id not in session.workers or (
                            entry.tool_call_id == call.tool_call_id and entry.state.status == "input_required"
                        ))
            if entry.tool_call_id == call.tool_call_id:
                if entry.state.status == "completed":
                    return portable_result(entry)
                if entry.state.status == "input_required":
                    result = {"receipt": result, "status": entry.state.status, "task_id": entry.id, "question": entry.text}
            return ToolReturn(result, metadata={"remote_agent": entry.summary()})
        finally:
            self.task_for_call.pop(call.tool_call_id, None)
            SessionTransport.step.reset(token)

    async def remote_task_result(self, ctx: RunContext[AgentDeps], task_id: str, refresh: bool = False) -> ToolReturn:
        """Read the status, result and generated files of a remote task in this conversation."""
        session = SessionTransport.current.get()
        entry = session.entries.get(task_id) if session is not None else None
        if entry is None:
            raise ModelRetry("No such remote task in this conversation.")
        try:
            request = RequestScope.from_deps(ctx.deps, self.connection_ids)
            await self.runtime.authorize(entry, request)
            if refresh and task_id not in session.workers:
                await self.runtime.refresh(entry, request)
        except RemoteAgentError as error:
            raise ModelRetry(str(error)) from error
        return ToolReturn(entry.result_payload(), metadata={"remote_agent": entry.summary()})


def portable_result(entry) -> ToolReturn:
    return ToolReturn(entry.result_payload(), metadata={
        "generated_files": entry.result_files(), "remote_agent": entry.summary(),
    })


def remote_capabilities(runtime, connections):
    return [
        RemoteLifecycle(runtime, connections),
        SubAgents(task_control=TaskControl(
            remote_agents=[RemoteSubAgent(name=_target(row.id),
                description=f"{row.name} ({row.config['protocol']}). {row.description or ''}") for row in connections],
            transport=SessionTransport(), max_running_tasks=4,
        ), max_depth=2),
    ]
