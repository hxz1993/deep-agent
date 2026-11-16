from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from contextvars import ContextVar
from datetime import UTC, datetime

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

from .contracts import RemoteAgentError
from .runtime import RequestScope


def _target(connection_id: str) -> str:
    return "remote_" + connection_id.replace("-", "_")


class HarnessSession(InMemoryTransport):
    def __init__(self, runtime, request):
        super().__init__()
        self.runtime = runtime
        self.request = request
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
                self.workers[message.task_id] = asyncio.create_task(self._work(message))
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

    async def _work(self, event: TaskRequestEvent):
        identifiers = {"task_id": event.task_id, "agent_name": event.agent_name}
        entry = self.entries.get(event.task_id)
        request = self.request
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
                mode, prompt = "answer", request.user_message
            else:
                mode, prompt = "resume", event.prompt
            while True:
                await self.runtime.execute(entry, request, prompt, mode)
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
                        candidate = self.request
                        if (isinstance(answer, TaskAnsweredEvent) and answer.question_id == question_id
                                and candidate.run_id != entry.run_id and candidate.user_message
                                and candidate.remote_task_id in (None, entry.id)
                                and candidate.remote_question_id in (None, question_id)):
                            request, prompt = candidate, candidate.user_message
                            break
                mode = "answer"
            if entry.state.status == "completed":
                output = entry.text
                if entry.files or entry.warnings:
                    output += f"\nUse remote_task_result(task_id={entry.id!r}) to retrieve the generated files and notices."
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
            await self.release(f"task:{event.task_id}:worker")
            async with self.advanced:
                self.advanced.notify_all()


class SessionTransport(InMemoryTransport):
    current = ContextVar("deep_agent_remote_session", default=None)

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
            "Use remote_task_result if the decision or question text is not yet in the tool receipt. "
            "Do not invent answers. A normal user reply continues the sole waiting "
            "delegation. For multiple waiting tasks use answer_task only for the user's chosen task. "
            "If a waiting task was restored after a restart, delegate with resume=<original task id> and the "
            "same agent, using the user's reply. To attach delegated files to this conversation, call "
            "remote_task_result after the task completes, and present those files to the user. "
            "Use delegate_task with resume for a new question that continues a completed remote conversation."
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
            session = HarnessSession(self.runtime, request)
            self.runtime.harness_sessions[key] = session
            await session.open()
        session.active_runs += 1
        try:
            session.request = request
            session.owner.aliases.clear()
            session.owner.aliases.update(self.target_aliases)
            await self._restore(ctx.conversation_id, session, request)
            token = SessionTransport.current.set(session)
            try:
                with session.owner.bind():
                    await self._answer_user_reply(ctx, session, request)
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

    async def _answer_user_reply(self, ctx, session, request):
        if not request.user_message:
            return
        waiting = [(entry, session.owner.pending_question(entry.id)) for entry in session.entries.values()
                   if entry.state.status == "input_required" and entry.run_id != request.run_id
                   and entry.connection_id in self.connection_ids
                   and request.remote_task_id in (None, entry.id)]
        if len(waiting) != 1:
            if request.remote_task_id:
                ctx.enqueue("所选远程任务在当前会话中没有待回答的问题，本轮未向该任务发送补答。")
            return
        entry, question = waiting[0]
        try:
            await self.runtime.authorize(entry, request)
            if request.remote_question_id not in (None, entry.state.question_id):
                raise RemoteAgentError("远程补问已更新，请刷新后重新选择问题")
        except RemoteAgentError as error:
            if request.remote_task_id:
                ctx.enqueue(str(error))
            return
        if question is None and entry.id not in session.workers:
            await self._answer_recovered(ctx, entry, request)
        elif question and session.owner.answer(entry.id, question_id=question[0], answer=request.user_message):
            with suppress(TimeoutError):
                async with asyncio.timeout(75), session.advanced:
                    await session.advanced.wait_for(lambda: entry.id not in session.workers or (
                        entry.run_id == request.run_id and entry.state.status == "input_required"
                        and entry.state.question_id != question[0]
                    ))
            self._publish_answer_state(ctx, entry)

    async def _answer_recovered(self, ctx, entry, request) -> None:
        previous_question = entry.state.question_id
        try:
            await self.runtime.refresh(entry, request)
            if entry.state.status == "input_required" and entry.state.question_id == previous_question:
                await self.runtime.execute(entry, request, request.user_message, "answer")
        except RemoteAgentError as error:
            ctx.enqueue("远程任务暂时无法续跑。运行时返回：" + str(error))
            return
        self._publish_answer_state(ctx, entry)

    @staticmethod
    def _publish_answer_state(ctx, entry):
        ctx.enqueue("<remote_task_update>" + json.dumps({
            "task_id": entry.id, "status": entry.state.status,
            "question": entry.text if entry.state.status == "input_required" else None,
            "has_files": bool(entry.files), "warnings": entry.warnings,
        }, ensure_ascii=False) + "</remote_task_update>")

    @on_event(TaskAnsweredEvent)
    async def validate_answer(self, ctx, event):
        session = SessionTransport.current.get()
        entry = session.entries.get(event.task_id) if session is not None else None
        if entry is not None and (entry.run_id == ctx.deps.run_id or not ctx.deps.remote_user_message):
            event.veto("Wait for the user to answer this question in the next turn.")
        elif ctx.deps.remote_task_id is not None and ctx.deps.remote_task_id != event.task_id:
            event.veto("The user chose a different task for this reply.")
        elif entry is not None and entry.connection_id not in self.connection_ids:
            event.veto("This remote connection is disabled for the current turn.")
        elif ctx.deps.remote_question_id not in (None, event.question_id):
            event.veto("The chosen remote question is no longer pending.")

    @on_event(TaskSteeredEvent)
    async def unsupported_steering(self, ctx, event):
        event.veto("This agent accepts replies to its questions, or a follow-up after its current work ends.")

    @on_event(TaskRequestEvent)
    async def track_delegation(self, ctx, event):
        if ctx.tool_call_id:
            self.task_for_call[ctx.tool_call_id] = event.task_id

    async def wrap_tool_execute(self, ctx, *, call, tool_def, args, handler):
        result = await handler(args)
        task_id = self.task_for_call.pop(call.tool_call_id, None)
        session = SessionTransport.current.get()
        entry = session.entries.get(task_id) if task_id and session is not None else None
        if entry is None:
            return result
        generated = list(entry.files.values()) if entry.state.status == "completed" else []
        if entry.state.status == "input_required":
            result = {"receipt": result, "status": entry.state.status, "task_id": entry.id, "question": entry.text}
        return ToolReturn(result, metadata={"remote_agent": entry.summary(), "generated_files": generated})

    async def remote_task_result(self, ctx: RunContext[AgentDeps], task_id: str, refresh: bool = False) -> ToolReturn:
        """Read the status, result and generated files of a remote task in this conversation."""
        session = SessionTransport.current.get()
        entry = session.entries.get(task_id) if session is not None else None
        if entry is None:
            raise ModelRetry("No such remote task in this conversation.")
        try:
            await self.runtime.authorize(entry, session.request)
            missing_result = entry.state.status == "completed" and not entry.text and not entry.files
            if (refresh or missing_result) and task_id not in session.workers:
                await self.runtime.refresh(entry, session.request)
        except RemoteAgentError as error:
            raise ModelRetry(str(error)) from error
        return portable_result(entry)


def portable_result(entry) -> ToolReturn:
    return ToolReturn(
        {"task_id": entry.id, "status": entry.state.status, "result": entry.text,
         "files": list(entry.files.values()), "warnings": entry.warnings},
        metadata={"generated_files": list(entry.files.values()), "remote_agent": entry.summary()},
    )


def remote_capabilities(runtime, connections):
    return [
        RemoteLifecycle(runtime, connections),
        SubAgents(task_control=TaskControl(
            remote_agents=[RemoteSubAgent(name=_target(row.id),
                description=f"{row.name} ({row.config['protocol']}). {row.description or ''}") for row in connections],
            transport=SessionTransport(), max_running_tasks=4,
        ), max_depth=2),
    ]
