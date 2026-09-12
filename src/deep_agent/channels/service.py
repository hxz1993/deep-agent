"""Transport-independent channel scheduling and command execution."""
from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from deep_agent.agent.observability import log_event
from deep_agent.core.exceptions import AgentModelNotConfiguredError

from .commands import help_text, parse_command
from .contracts import ChannelAdapter, InboundMessage


@dataclass
class MessageJob:
    account: Any
    adapter: ChannelAdapter
    message: InboundMessage
    run_id: str
    reply_id: str
    order: int
    session_id: str | None = None
    agent_id: str | None = None
    dispatched: bool = False
    stopped: bool = False
    terminal: bool = False
    task: asyncio.Task | None = None
    agent_run_id: str | None = None


def _log_job(event: str, job: MessageJob, **fields: Any) -> None:
    log_event(event, account_id=job.account.id, channel_run_id=job.run_id,
              run_id=job.agent_run_id, session_id=job.session_id,
              agent_id=job.agent_id, **fields)


@dataclass
class PeerQueue:
    pending: deque[MessageJob] = field(default_factory=deque)
    active: MessageJob | None = None
    ingress: asyncio.Lock = field(default_factory=asyncio.Lock)
    control: asyncio.Lock = field(default_factory=asyncio.Lock)
    order: int = 0
    controls: int = 0
    blocked: bool = False
    resume_task: asyncio.Task | None = None
    worker: asyncio.Task | None = None


class ChannelService:
    def __init__(self, store, deep_agent, *, stream_interval: float = 0.5, stop_timeout: float = 15) -> None:
        self.store = store
        self.deep_agent = deep_agent
        self.stream_interval = stream_interval
        self.stop_timeout = stop_timeout
        self.peers: dict[tuple[str, str, str], PeerQueue] = {}
        self._closed: set[str] = set()
        self._receivers: dict[asyncio.Task, str] = {}

    def open_account(self, account_id: str) -> None:
        self._closed.discard(account_id)

    async def receive(self, account, adapter: ChannelAdapter, message: InboundMessage) -> None:
        key = (account.id, message.peer_type, message.peer_id)
        if account.id in self._closed:
            return
        peer = self.peers.setdefault(key, PeerQueue())
        peer.order += 1
        order = peer.order
        command = parse_command(message)
        control = command in {"/stop", "/new"}
        if control:
            # Gate dispatch before the first database await. Control messages
            # never wait behind the ordinary Agent worker.
            peer.controls += 1
        task = asyncio.current_task()
        if task:
            self._receivers[task] = account.id
        try:
            log_event("channel_received", account_id=account.id)
            async with peer.ingress:
                account = await self.store.get_account(account.id)
                if account is None or not account.enabled or account.id in self._closed:
                    return
                previous = await self.store.get_message(account.id, message.message_id)
                if previous is not None and not self.store.can_retry(previous):
                    return
                binding = await self.store.ensure_binding(
                    account_id=account.id, peer_type=message.peer_type,
                    peer_id=message.peer_id, peer_metadata=message.peer_metadata,
                )
                reply_id = adapter.new_reply_id()
                claimed = await self.store.claim_message(
                    account_id=account.id, binding_id=binding.id,
                    external_message_id=message.message_id,
                    external_request_id=message.request_id, sender_id=message.sender_id,
                    session_id=None, payload_hash=message.payload_hash, stream_id=reply_id,
                )
                if not claimed.claimed:
                    return
                job = MessageJob(account, adapter, message, claimed.run.id, reply_id, order)
                if command is None:
                    await self.store.update_run(job.run_id, processing_status="queued")
                    peer.pending.append(job)
                    _log_job("channel_queued", job, queue_depth=len(peer.pending))
                    self._kick(peer)
                    return
            if control:
                async with peer.control:
                    await self._control(peer, job, command)
            else:
                await self._read_command(peer, job, command)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log_event("channel_ingress_failed", account_id=account.id, error_type=type(exc).__name__)
        finally:
            if control:
                peer.controls -= 1
                self._kick(peer)
            if task:
                self._receivers.pop(task, None)

    def _kick(self, peer: PeerQueue) -> None:
        if peer.controls or peer.blocked or not peer.pending:
            return
        if peer.worker is None or peer.worker.done():
            peer.worker = asyncio.create_task(self._work(peer), name="channel-peer-worker")

    async def _work(self, peer: PeerQueue) -> None:
        try:
            while peer.pending and not peer.controls and not peer.blocked:
                job = peer.pending.popleft()
                peer.active = job
                job.task = asyncio.create_task(self._process(job), name="channel-agent-message")
                try:
                    await job.task
                except asyncio.CancelledError:
                    if not job.stopped:
                        raise
                finally:
                    peer.active = None
        finally:
            peer.worker = None
            self._kick(peer)

    async def _process(self, job: MessageJob) -> None:
        # Public text phases accumulate only in the temporary preview. Keep the
        # current phase separate for legacy final events without an output.
        preview = ""
        previous_previews = ""
        final_output: str | None = None
        loading_started = False
        stream = None
        try:
            account = await self.store.get_account(job.account.id)
            if account is None or not account.enabled or account.id in self._closed:
                job.stopped = True
                raise asyncio.CancelledError
            job.account = account
            resolved = await self.store.resolve_binding(
                account_id=account.id, peer_type=job.message.peer_type,
                peer_id=job.message.peer_id, peer_metadata=job.message.peer_metadata,
            )
            # Session creation and its routing identity share one DB snapshot.
            # A concurrent account edit must not pair a new Session with the
            # previously loaded Agent/user configuration.
            account = resolved.account or account
            job.account = account
            job.session_id = resolved.session.session_id
            job.agent_id = resolved.session.agent_id
            await self.store.update_run(job.run_id, session_id=job.session_id)
            # Start native feedback before media/model I/O, and reuse this reply
            # ID for text and terminal replies so there is only one bubble.
            loading_started = True
            preview_enabled = await self._preview(job, loading=True)
            showing_loading = True
            attachments = []
            for media in job.message.media:
                data, filename = await job.adapter.download_media(media)
                attachments.append(await self.deep_agent.upload_media(account, media, data, filename or media.filename))
            input_options = {}
            if job.message.quote is not None:
                quote = job.message.quote
                quoted_attachments = []
                for media in quote.media:
                    data, filename = await job.adapter.download_media(media)
                    uploaded = await self.deep_agent.upload_media(account, media, data, filename or media.filename)
                    attachments.append(uploaded)
                    quoted_attachments.append({key: uploaded[key] for key in ("id", "name", "mime") if key in uploaded})
                # Reuse the existing context/history path. Never forward the
                # raw channel frame, media URL/key, or invented quote identity.
                input_options["context_refs"] = [{
                    "key": "channel_quote", "type": "channel.message_quote",
                    "schema_version": "1.0", "label": "引用消息",
                    "payload": {"message_type": quote.message_type, "text": quote.text,
                                "attachments": quoted_attachments},
                }]
            # Group authors vary per turn. Private identity is stable, so keep
            # it only on the first persisted User Event of each Session. A
            # title-only /new or failed preflight must not consume that turn;
            # persisted Events also make the decision survive gateway restarts.
            if job.message.peer_type != "single" or not await self.store.has_user_messages(job.session_id):
                input_options.setdefault("context_refs", []).append({
                    "key": "channel_source", "type": "channel.message_source",
                    "schema_version": "1.0", "label": f"发送者 · {job.message.sender_id}",
                    "payload": {
                        "channel_type": account.channel_type,
                        "account_id": account.id,
                        "peer_type": job.message.peer_type,
                        "peer_id": job.message.peer_id,
                        "sender_id": job.message.sender_id,
                    },
                })
            # Durable dispatch boundary: absence of an SSE run ID is not proof
            # that the API did not execute the request.
            await self.store.update_run(job.run_id, processing_status="running", delivery_status="streaming")
            job.dispatched = True
            _log_job("channel_dispatched", job)
            last_sent = 0.0
            final_status = "completed"
            stream = self.deep_agent.stream_chat(
                account, session_id=job.session_id, message=job.message.prompt, attachments=attachments,
                **input_options,
            )
            async for event in stream:
                if event.event == "session":
                    run_id = event.data.get("run_id")
                    if run_id:
                        await self.store.update_run(job.run_id, agent_run_id=str(run_id))
                        job.agent_run_id = str(run_id)
                        _log_job("channel_agent_linked", job)
                elif event.event == "text":
                    delta = event.data.get("delta")
                    if not isinstance(delta, str) or not delta:
                        continue
                    preview += delta
                    now = time.monotonic()
                    if showing_loading or now - last_sent >= self.stream_interval:
                        if preview_enabled:
                            content = f"{previous_previews}\n\n{preview.lstrip()}" if previous_previews else preview
                            preview_enabled = await self._preview(job, content)
                        last_sent = now
                    showing_loading = False
                elif event.event in {"thinking", "tool_call", "context_compaction_progress"}:
                    # Native tools use the same public tool_call event. Results
                    # and reasoning stay in Event Store, not the IM bubble.
                    # Waiting preserves all public phases, including text not
                    # yet sent by the throttle. Only a terminal replaces them.
                    if not showing_loading:
                        if preview.strip():
                            previous_previews = f"{previous_previews}\n\n{preview.strip()}" if previous_previews else preview.strip()
                        preview = ""
                        if preview_enabled:
                            preview_enabled = await self._preview(job, previous_previews, loading=True)
                        showing_loading = True
                elif event.event == "final":
                    job.terminal = True
                    final_status = str(event.data.get("status") or "completed")
                    if "output" in event.data:
                        value = event.data["output"]
                        final_output = value if isinstance(value, str) else ""
                elif event.event == "cancelled":
                    job.terminal = True
                    final_status = "interrupted"
                elif event.event == "error":
                    job.terminal = True
                    final_status = "failed"
                if job.terminal:
                    # Deliver immediately, without waiting for HTTP EOF. Late
                    # deltas or a transport failure cannot overwrite a final.
                    break
            if not job.terminal:
                raise RuntimeError("DeepAgent stream ended without terminal event")
            # Only old events that omit output may fall back to the last text
            # phase. Explicitly empty output must not resurrect process text.
            output = preview if final_output is None else final_output
            processing = "completed"
            if final_status in {"cancelled", "interrupted"}:
                processing = "interrupted"
                output = f"{output}\n\n已停止。".strip()
            elif final_status in {"error", "failed"}:
                processing = "failed"
                output = f"{output}\n\n处理失败，请在 DeepAgent 控制台查看详情。".strip()
            elif final_status == "pending":
                output = f"{output}\n\n此操作需要在 DeepAgent 控制台继续完成。".strip()
            await self.store.update_run(job.run_id, processing_status=processing)
            _log_job("channel_processing_failed" if processing == "failed" else
                     "channel_cancelled" if processing == "interrupted" else "channel_completed",
                     job, status=final_status)
            await self._deliver(job, output if output.strip() else "已处理完成，但没有可展示的文本结果。")
        except asyncio.CancelledError:
            _log_job("channel_cancelled", job)
            await self.store.update_run(
                job.run_id, processing_status="interrupted", delivery_status="failed",
                error_code="user_stopped" if job.stopped else ("gateway_cancelled" if job.dispatched else "preflight_failed"),
                error_message="Channel processing interrupted",
            )
            if loading_started:
                # /stop during media preparation or gateway shutdown may cancel
                # before the API emits a terminal SSE. Close the native spinner
                # while the adapter is still connected; don't delay shutdown.
                try:
                    async with asyncio.timeout(2):
                        notice = "已停止。" if job.stopped else "服务已停止，请稍后重试。"
                        await self._deliver(job, f"{preview}\n\n{notice}".strip())
                except TimeoutError:
                    logger.warning("Channel loading completion timed out during cancellation")
            raise
        except Exception as exc:
            _log_job("channel_processing_failed", job, error_type=type(exc).__name__)
            missing_model = isinstance(exc, AgentModelNotConfiguredError)
            await self.store.update_run(
                job.run_id, processing_status="failed", delivery_status="failed",
                error_code="model_not_configured" if missing_model else ("agent_failed" if job.dispatched else "preflight_failed"),
                error_message="Agent 尚未配置模型" if missing_model else "Channel message processing failed",
            )
            await self._deliver(job, "Agent 尚未配置模型，请在 DeepAgent 控制台配置模型。" if missing_model else "消息处理失败，请稍后重试或在 DeepAgent 控制台查看状态。")
        finally:
            if stream is not None:
                try:
                    async with asyncio.timeout(2):
                        await stream.aclose()
                except Exception as exc:
                    logger.warning("Channel stream cleanup failed error_type={}", type(exc).__name__)

    async def _preview(self, job: MessageJob, content: str = "", *, loading: bool = False) -> bool:
        """Best-effort progress; a failed preview must not abort/replay a Run."""
        try:
            if loading:
                await job.adapter.reply_loading(job.message, job.reply_id, content)
            else:
                await job.adapter.reply(job.message, job.reply_id, content, False)
        except Exception as exc:
            # Stop preview attempts for this Run rather than waiting for an SDK
            # timeout on every delta. Still attempt the final delivery once.
            _log_job("channel_preview_failed", job, error_type=type(exc).__name__)
            return False
        return True

    async def _deliver(self, job: MessageJob, text: str) -> None:
        try:
            await job.adapter.reply(job.message, job.reply_id, text, True)
        except Exception as exc:
            _log_job("channel_delivery_failed", job, error_type=type(exc).__name__)
            # Never turn a completed /new into a retryable operation because
            # the network lost its acknowledgement.
            await self.store.update_run(job.run_id, delivery_status="failed", error_code="delivery_failed", error_message="Channel reply delivery failed")
        else:
            await self.store.update_run(job.run_id, delivery_status="sent")
            _log_job("channel_delivery_sent", job)

    async def _read_command(self, peer: PeerQueue, job: MessageJob, command: str) -> None:
        await self.store.update_run(job.run_id, processing_status="running")
        try:
            binding = await self.store.get_binding(job.account.id, job.message.peer_type, job.message.peer_id)
            session_id = binding.session_id if binding else None
            if command == "/help":
                text = help_text()
            elif command == "unknown":
                text = "未知命令或参数。请单独发送以下命令：\n\n" + help_text()
            elif command == "/whoami":
                text = f"User ID：{job.message.sender_id}"
                if job.message.peer_type == "group":
                    text += f"\nChat ID：{job.message.peer_id}"
                text += f"\n会话用户：{job.account.session_user_id}"
            else:
                facts = await self.store.account_summary(job.account.id)
                state = "idle"
                if session_id:
                    state = (await self.deep_agent.run_status(job.account, session_id)).get("status", "idle")
                label = {"idle": "空闲", "running": "运行中", "cancel_requested": "停止中"}.get(state, "未知")
                if peer.blocked:
                    label = "停止确认中"
                elif peer.active and state == "idle":
                    label = "准备中"
                text = f"Agent：{facts['agent_name']}\n默认模型：{facts['model_name']}\n会话：{session_id or '尚未创建'}\n任务：{label}\n等待消息：{len(peer.pending)}"
            await self.store.update_run(job.run_id, processing_status="completed", session_id=session_id)
            await self._deliver(job, text)
        except Exception as exc:
            logger.warning("Channel command failed error_type={}", type(exc).__name__)
            await self.store.update_run(job.run_id, processing_status="failed", error_code="command_failed", error_message="Channel command failed")
            await self._deliver(job, "命令执行失败，请稍后重试。")

    async def _control(self, peer: PeerQueue, job: MessageJob, command: str) -> None:
        active = None
        session_id = None
        await self.store.update_run(job.run_id, processing_status="running")
        try:
            discarded = [item for item in peer.pending if item.order < job.order]
            peer.pending = deque(item for item in peer.pending if item.order >= job.order)
            for item in discarded:
                await self.store.update_run(item.run_id, processing_status="interrupted", delivery_status="failed", error_code="user_stopped", error_message="Queued message cancelled by channel command")
            binding = await self.store.get_binding(job.account.id, job.message.peer_type, job.message.peer_id)
            active = peer.active
            session_id = active.session_id if active else (binding.session_id if binding else None)
            if active:
                active.stopped = True
                if not active.dispatched and active.task:
                    active.task.cancel()
                    # A Task cancelled before its first execution never enters
                    # _process's try/finally. Persist this at the control owner.
                    await self.store.update_run(active.run_id, processing_status="interrupted", delivery_status="failed", error_code="user_stopped", error_message="Message cancelled before dispatch")
            peer.blocked = True
            stopped = await self._wait_stopped(job.account, session_id, active, self.stop_timeout)
            if not stopped:
                await self.store.update_run(job.run_id, processing_status="failed", error_code="stop_timeout", error_message="Waiting for task cancellation")
                await self._deliver(job, "仍在停止中，尚未切换会话。请稍后再试；确认停止前不会派发新消息。")
                if peer.resume_task is None or peer.resume_task.done():
                    peer.resume_task = asyncio.create_task(self._resume_when_stopped(peer, job.account, session_id, active))
                return
            peer.blocked = False
            if command == "/new":
                if job.account.id in self._closed:
                    raise asyncio.CancelledError
                resolved = await self.store.rotate_binding(
                    account_id=job.account.id, peer_type=job.message.peer_type,
                    peer_id=job.message.peer_id, peer_metadata=job.message.peer_metadata,
                    command_run_id=job.run_id,
                )
                await self._deliver(job, f"已新建会话，原会话和历史仍保留。\n会话 ID：{resolved.session.session_id}")
            else:
                await self.store.update_run(job.run_id, processing_status="completed", session_id=session_id)
                text = "已停止，保留当前会话。" if active or session_id else "当前没有正在运行的任务。"
                if discarded:
                    text += f"已取消 {len(discarded)} 条等待消息。"
                await self._deliver(job, text)
        except Exception as exc:
            logger.warning("Channel control failed error_type={}", type(exc).__name__)
            await self.store.update_run(job.run_id, processing_status="failed", error_code="command_failed", error_message="Channel control failed")
            await self._deliver(job, "无法确认任务停止，未切换会话，请稍后重试。")
            # Do not release a potentially still-running peer on an API error.
            if peer.blocked and (peer.resume_task is None or peer.resume_task.done()):
                peer.resume_task = asyncio.create_task(self._resume_when_stopped(peer, job.account, session_id, active))

    async def _wait_stopped(self, account, session_id: str | None, active: MessageJob | None, timeout: float) -> bool:
        try:
            async with asyncio.timeout(timeout):
                return await self._poll_stopped(account, session_id, active)
        except TimeoutError:
            return False

    async def _poll_stopped(self, account, session_id, active) -> bool:
        while True:
            local_done = active is None or active.task is None or active.task.done()
            current_session = (active.session_id if active else None) or session_id
            if current_session:
                # Deleting a Session removes its control API lookup. Only an
                # explicit terminal SSE or a never-dispatched local job proves
                # that a missing Session is safe to release; HTTP 404 alone
                # cannot establish that the remote Agent stopped.
                if local_done and active and (active.terminal or not active.dispatched):
                    existing = await self.store.get_channel_session(account.id, current_session)
                    if existing is None:
                        return True
                await self.deep_agent.cancel_session(account, current_session, agent_id=active.agent_id if active else None)
                status = await self.deep_agent.run_status(account, current_session, agent_id=active.agent_id if active else None)
                remote_done = status.get("status") == "idle"
            else:
                remote_done = True
            if local_done and remote_done:
                return True
            await asyncio.sleep(0.1)

    async def _resume_when_stopped(self, peer: PeerQueue, account, session_id, active) -> None:
        while account.id not in self._closed:
            try:
                if await self._wait_stopped(account, session_id, active, 1):
                    peer.blocked = False
                    self._kick(peer)
                    return
            except Exception as exc:
                logger.warning("Channel cancellation confirmation failed error_type={}", type(exc).__name__)
            await asyncio.sleep(1)

    async def close_account(self, account_id: str) -> None:
        self._closed.add(account_id)
        # Block dispatch before cancelling ingress: a control callback's finally
        # calls _kick and must not launch a waiting message during shutdown.
        for key, peer in self.peers.items():
            if key[0] == account_id:
                peer.blocked = True
        receivers = [task for task, owner in self._receivers.items() if owner == account_id and task is not asyncio.current_task()]
        for task in receivers:
            task.cancel()
        if receivers:
            await asyncio.gather(*receivers, return_exceptions=True)
        for key, peer in list(self.peers.items()):
            if key[0] != account_id:
                continue
            peer.blocked = True
            if peer.active and peer.active.dispatched and peer.active.session_id:
                try:
                    await self.deep_agent.cancel_session(peer.active.account, peer.active.session_id, agent_id=peer.active.agent_id)
                except Exception:
                    pass
            for pending in peer.pending:
                try:
                    await self.store.update_run(pending.run_id, processing_status="interrupted", delivery_status="failed", error_code="preflight_failed", error_message="Channel stopped before dispatch")
                except Exception:
                    logger.warning("Unable to persist queued shutdown status; restart recovery will reconcile it")
            peer.pending.clear()
            tasks = [task for task in (peer.worker, peer.resume_task) if task and not task.done()]
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            self.peers.pop(key, None)
