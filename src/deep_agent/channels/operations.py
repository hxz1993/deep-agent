"""Current-chat sends with call-local results; no separate receipt persistence."""
from __future__ import annotations

import asyncio
import hashlib
from dataclasses import asdict
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .message_payloads import validate_message_payload
from .outbound import DeliveryError, PreparedComponent, resolve_run_context


class WireComponent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "image", "file", "audio", "video", "textcard", "news"]
    requested_kind: Literal["text", "image", "file", "audio", "video", "textcard", "news"]
    text: str = Field(default="", max_length=20000)
    filename: str = Field(default="", max_length=200)
    mime: str = Field(default="application/octet-stream", max_length=200)

    payload: dict | None = None

    @model_validator(mode="after")
    def validate_payload(self):
        if self.kind in {"textcard", "news"}:
            if self.text or self.filename or self.requested_kind != self.kind:
                raise ValueError("卡片内容字段无效")
            self.payload = validate_message_payload(self.kind, self.payload)
        elif self.payload is not None:
            raise ValueError("此消息类型不支持payload")
        return self


class MessageOperationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    account_id: str = Field(max_length=128)
    channel_run_id: str = Field(max_length=128)
    session_id: str = Field(max_length=128)
    agent_id: str = Field(max_length=128)
    user_id: str = Field(max_length=128)
    agent_run_id: str = Field(max_length=128)
    reply: bool = False
    components: list[WireComponent] = Field(min_length=1, max_length=10)


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
                or job.run_id != source.channel_run_id or row is None
                or row.agent_run_id != request.agent_run_id):
            raise DeliveryError("run_inactive", "本轮渠道运行已结束或连接已变更")
        account = await self.store.get_account(source.account_id)
        if account is None or self.runtime.registry.fingerprint(account) != managed.fingerprint:
            raise DeliveryError("account_changed", "渠道连接配置已变更")
        status = managed.adapter.status()
        ready = status.get("state") == "connected" if account.channel_type == "wecom_app" else status.get("authenticated")
        if not ready:
            raise DeliveryError("channel_offline", "渠道暂未连接，请稍后再试")
        return source, managed, job

    async def send(self, request: MessageOperationRequest, components: list[PreparedComponent]):
        from .message_tool import normalize_media

        source, managed, job = await self._authorize(request)
        caps = managed.adapter.message_capabilities()
        if request.reply and not caps.supports_reply:
            raise DeliveryError("quote_unsupported", "当前渠道不支持原生引用回复")
        components = [normalize_media(item, caps) for item in components]
        results = []
        for index, item in enumerate(components):
            result = {"index": index, "kind": item.kind, "requested_kind": item.requested_kind, "state": "pending"}
            if item.kind != item.requested_kind:
                result["note"] = "此格式或大小不适合原生媒体消息，已改为文件发送"
            results.append(result)
        task = asyncio.current_task()
        self.tasks[task] = source.account_id
        job.outbound_tasks.add(task)
        current_index = -1
        state = "completed"
        try:
            try:
                for index, item in enumerate(components):
                    current_index = index
                    current_source, current_managed, _ = await self._authorize(request)
                    if current_source != source or current_managed is not managed:
                        raise DeliveryError("connection_changed", "渠道连接已变更，停止发送")
                    results[index]["state"] = "running"
                    try:
                        async with asyncio.timeout(120):
                            receipt = await managed.adapter.send_component(source.target, item, reply_to_current=request.reply)
                    except TimeoutError:
                        raise DeliveryError("send_timeout", "发送超时，结果未知，请勿重复发送", unknown=True) from None
                    results[index].update({k: v for k, v in asdict(receipt).items() if v is not None})
                    if receipt.kind != item.requested_kind:
                        results[index]["note"] = "此格式或大小不适合原生媒体消息，已改为文件发送"
                    results[index]["state"] = "succeeded"
                    if item.kind == "text":
                        job.sent_text_hashes.add(hashlib.sha256(item.text.strip().encode()).hexdigest())
                    job.message_tool_sent = True
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                error = exc if isinstance(exc, DeliveryError) else DeliveryError("delivery_unconfirmed", "投递异常，结果无法确认", unknown=True)
                state = "unknown" if error.unknown else "failed"
                if current_index >= 0 and results[current_index]["state"] != "succeeded":
                    results[current_index].update(state=state, error_code=error.code, error_message=str(error))
                for result in results:
                    if result["state"] == "pending":
                        result["state"] = "skipped"
            return {"status": state, "components": results,
                    "visible_to_current_chat": any(r["state"] == "succeeded" for r in results)}
        finally:
            self.tasks.pop(task, None)
            job.outbound_tasks.discard(task)
