"""Durable, current-chat outbound execution owned by the channel gateway."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .outbound import DeliveryError, PreparedComponent, request_hash, resolve_run_context


class WireComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "image", "file", "audio", "video"]
    requested_kind: Literal["text", "image", "file", "audio", "video"]
    text: str = Field(default="", max_length=20000)
    filename: str = Field(default="", max_length=200)
    mime: str = Field(default="application/octet-stream", max_length=200)


class MessageOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(max_length=128)
    channel_run_id: str = Field(max_length=128)
    session_id: str = Field(max_length=128)
    agent_id: str = Field(max_length=128)
    user_id: str = Field(max_length=128)
    agent_run_id: str = Field(max_length=128)
    tool_call_id: str = Field(min_length=1, max_length=191)
    reply: bool = False
    components: list[WireComponent] = Field(min_length=1, max_length=10)


def operation_result(record):
    return {"operation_id": record.id, "status": record.state, "components": record.results,
            "visible_to_current_chat": any(r.get("state") == "succeeded" for r in record.results)}


class ChannelOperationService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.store = runtime.store
        self.tasks: dict[asyncio.Task, str] = {}

    async def close_account(self, account_id):
        tasks = [task for task, account in self.tasks.items() if account == account_id]
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _authorize(self, request):
        source = await resolve_run_context(self.store, account_id=request.account_id,
            channel_run_id=request.channel_run_id, session_id=request.session_id,
            agent_id=request.agent_id, user_id=request.user_id)
        managed = self.runtime._accounts.get(source.account_id)
        row = await self.store.get_run(source.channel_run_id)
        peer = self.runtime.service.peers.get((source.account_id, source.target.peer_type, source.target.peer_id))
        job = peer.active if peer else None
        if (managed is None or managed.stopping or job is None or job.stopped or job.terminal
                or job.run_id != source.channel_run_id or row.agent_run_id != request.agent_run_id):
            raise DeliveryError("run_inactive", "本轮渠道运行已结束或连接已变更")
        account = await self.store.get_account(source.account_id)
        if self.runtime.registry.fingerprint(account) != managed.fingerprint:
            raise DeliveryError("account_changed", "渠道连接配置已变更")
        if not managed.adapter.status().get("authenticated"):
            raise DeliveryError("channel_offline", "渠道暂未连接，请稍后再试")
        return source, managed, job

    async def send(self, request: MessageOperationRequest, components: list[PreparedComponent]):
        from .message_tool import normalize_media

        source, managed, job = await self._authorize(request)
        caps = managed.adapter.message_capabilities()
        if request.reply and not caps.supports_reply:
            raise DeliveryError("quote_unsupported", "当前渠道不支持原生引用回复")
        components = [normalize_media(item, caps) for item in components]
        initial = []
        for index, item in enumerate(components):
            result = {"index": index, "kind": item.kind, "requested_kind": item.requested_kind, "state": "pending"}
            if item.kind == "text":
                result["text_hash"] = hashlib.sha256(item.text.strip().encode()).hexdigest()
            if item.kind != item.requested_kind:
                result["note"] = "此格式或大小不适合原生媒体消息，已改为文件发送"
            initial.append(result)
        try:
            record, created = await self.store.claim_operation(account_id=source.account_id,
                channel_run_id=source.channel_run_id, agent_run_id=request.agent_run_id,
                tool_call_id=request.tool_call_id, session_id=source.session_id,
                request_hash=request_hash(components, request.reply), results=initial)
        except ValueError:
            raise DeliveryError("operation_conflict", "工具调用重复且内容不一致，未重新发送") from None
        if not created:
            return operation_result(record)
        results = initial
        task = asyncio.current_task()
        self.tasks[task] = source.account_id
        job.outbound_tasks.add(task)
        current_index = -1
        state = "completed"
        try:
            try:
                await self.store.update_operation(record.id, state="running", results=results)
                for index, item in enumerate(components):
                    current_index = index
                    current_source, current_managed, _ = await self._authorize(request)
                    if current_source != source or current_managed is not managed:
                        raise DeliveryError("connection_changed", "渠道连接已变更，停止发送")
                    results[index]["state"] = "running"
                    await self.store.update_operation(record.id, state="running", results=results)
                    try:
                        async with asyncio.timeout(120):
                            receipt = await managed.adapter.send_component(source.target, item, reply_to_current=request.reply)
                    except TimeoutError:
                        raise DeliveryError("send_timeout", "发送超时，结果未知，请勿重复发送", unknown=True) from None
                    results[index].update({k: v for k, v in asdict(receipt).items() if v is not None})
                    results[index]["state"] = "succeeded"
                    # Only confirmed text hashes suppress exact duplicate finals.
                    if item.kind == "text":
                        job.sent_text_hashes.add(results[index]["text_hash"])
                    job.message_tool_sent = True
                    await self.store.update_operation(record.id, state="running", results=results)
            except asyncio.CancelledError:
                state = "cancelled"
                if current_index >= 0 and results[current_index]["state"] == "running":
                    results[current_index].update(state="unknown", error_code="cancelled_during_send")
                    state = "unknown"
                for result in results:
                    if result["state"] == "pending":
                        result["state"] = "cancelled"
                await self._persist_terminal(record.id, state=state, results=results)
                raise
            except Exception as exc:
                error = exc if isinstance(exc, DeliveryError) else DeliveryError("delivery_unconfirmed", "投递异常，结果无法确认", unknown=True)
                state = "unknown" if error.unknown else "failed"
                if current_index >= 0 and results[current_index]["state"] != "succeeded":
                    results[current_index].update(state=state, error_code=error.code, error_message=str(error))
                for result in results:
                    if result["state"] == "pending":
                        result["state"] = "skipped"
            updated = await self._persist_terminal(record.id, state=state, results=results)
            if updated is None:
                raise DeliveryError("operation_removed", "渠道已删除，发送回执无法保存，请勿重复发送", unknown=True)
            return operation_result(updated)
        finally:
            # Keep the task registered until the durable terminal write settles;
            # closing an account must await this write, including after cancel.
            self.tasks.pop(task, None)
            job.outbound_tasks.discard(task)

    async def _persist_terminal(self, operation_id, *, state, results):
        """Join the shielded write even if shutdown cancels us repeatedly."""
        write = asyncio.create_task(self.store.update_operation(operation_id, state=state, results=results))
        cancelled = False
        while not write.done():
            try:
                await asyncio.shield(write)
            except asyncio.CancelledError:
                cancelled = True
        result = write.result()
        if cancelled:
            raise asyncio.CancelledError
        return result
