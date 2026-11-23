"""Outbound A2A JSON-RPC."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator, Sequence
from contextlib import aclosing, asynccontextmanager
from dataclasses import replace
from pathlib import PurePosixPath
from urllib.parse import urljoin

import httpx
from a2a import types as wire
from a2a.client import A2AClientError, Client, ClientConfig, ClientFactory
from a2a.client.card_resolver import parse_agent_card
from google.protobuf.json_format import MessageToDict, ParseError

from .contracts import (
    A2AConfig, Checkpoint, InputFile, Operation, OutputFile, RemoteAgentError,
    RemoteResult, RemoteState, RemoteTaskExpired, Status,
)

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 64 * 1024 * 1024
MAX_CARD_BYTES = 1024 * 1024
MAX_ARTIFACTS = 128
MAX_TEXT = 64 * 1024
OBSERVATION_SECONDS = 60
RECOVERY_SECONDS = 10
ARTIFACT_SECONDS = 45
ACTIVE = frozenset({"submitted", "working"})
UNCONFIRMED_ANSWER = "暂时无法确认补答是否送达，远端仍显示原问题。"


def _validate_url(url: str) -> httpx.URL:
    try:
        parsed = httpx.URL(url)
        if (parsed.scheme not in {"http", "https"} or not parsed.host or parsed.userinfo
                or parsed.fragment or any(character.isspace() for character in url)):
            raise ValueError
    except (httpx.InvalidURL, TypeError, ValueError):
        raise RemoteAgentError("远端返回了无效的 HTTP(S) 地址") from None
    return parsed


def _origin(url: httpx.URL) -> tuple[str, str, int]:
    return url.scheme, url.host, url.port or {"http": 80, "https": 443}[url.scheme]


def _http_client(headers: dict[str, str]) -> httpx.AsyncClient:
    async def reject_redirect(response: httpx.Response) -> None:
        if response.is_redirect:
            raise RemoteAgentError("远程 Agent 接口返回重定向，请直接配置目标地址")

    return httpx.AsyncClient(
        headers=headers, follow_redirects=False, trust_env=False,
        timeout=httpx.Timeout(35, connect=10),
        event_hooks={"response": [reject_redirect]},
    )


async def _read_bytes(http: httpx.AsyncClient, url: str, maximum: int) -> tuple[bytes, str]:
    async with http.stream("GET", url, headers={"accept-encoding": "identity"}) as response:
        response.raise_for_status()
        if response.headers.get("content-encoding", "identity").lower() != "identity":
            raise RemoteAgentError("远端返回了未协商的压缩响应")
        limit = response.headers.get("content-length", "")
        if limit.isdecimal() and int(limit) > maximum:
            raise RemoteAgentError("远端响应超过大小限制")
        data = bytearray()
        async for chunk in response.aiter_bytes(chunk_size=65536):
            if len(data) + len(chunk) > maximum:
                raise RemoteAgentError("远端响应超过大小限制")
            data.extend(chunk)
        return bytes(data), response.headers.get("content-type", "")


async def _download(http: httpx.AsyncClient, endpoint: str, destination: str, maximum: int) -> tuple[bytes, str]:
    target = _validate_url(urljoin(endpoint, destination))
    if _origin(target) != _origin(_validate_url(endpoint)):
        raise RemoteAgentError("产物地址与已配置接口不同源")
    return await _read_bytes(http, str(target), maximum)


def _check_input(attachments: Sequence[InputFile]) -> None:
    if len(attachments) > 20 or sum(len(file.data) for file in attachments) > MAX_INPUT_BYTES:
        raise RemoteAgentError("单次发送最多 20 个附件，附件总量不超过 8 MiB")


def _name(value: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]", "_", PurePosixPath(value.replace("\\", "/")).name)[:240] or "artifact"


def _mime(value: str) -> str:
    value = value.partition(";")[0].strip()
    return value if re.fullmatch(r"[\w.+-]+/[\w.+-]+", value, flags=re.ASCII) else "application/octet-stream"


def _text_value(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _content_text(parts: Sequence[wire.Part]) -> str:
    values = []
    for part in parts:
        if part.HasField("text") and not part.filename:
            values.append(part.text)
        elif part.HasField("data") and not part.filename:
            values.append(_text_value(MessageToDict(part.data)))
    return "\n".join(values)[:MAX_TEXT]


def _wire_status(value: int) -> Status:
    return {
        wire.TaskState.TASK_STATE_SUBMITTED: "submitted",
        wire.TaskState.TASK_STATE_WORKING: "working",
        wire.TaskState.TASK_STATE_INPUT_REQUIRED: "input_required",
        wire.TaskState.TASK_STATE_COMPLETED: "completed",
        wire.TaskState.TASK_STATE_CANCELED: "canceled",
        wire.TaskState.TASK_STATE_AUTH_REQUIRED: "failed",
        wire.TaskState.TASK_STATE_REJECTED: "failed",
        wire.TaskState.TASK_STATE_FAILED: "failed",
    }.get(value, "unknown")


def _question_id(task_id: str, text: str) -> str:
    return hashlib.sha256(f"{task_id}\x00{text}".encode()).hexdigest()[:32]


async def _accept_state(state: RemoteState, updated: RemoteState, checkpoint: Checkpoint) -> RemoteState:
    if not updated.task_id:
        raise RemoteAgentError("远端响应缺少任务标识", status="unknown")
    if (state.task_id and state.task_id != updated.task_id
            or state.context_id and updated.context_id and state.context_id != updated.context_id):
        raise RemoteAgentError("远端返回的任务或会话与本次调用不一致", status="unknown")
    if state != updated:
        await checkpoint(updated)
    return updated


def _recovered_result(result: RemoteResult, unanswered: str | None) -> RemoteResult:
    if (unanswered and result.state.status == "input_required"
            and result.state.question_id == unanswered):
        return replace(result, warnings=(*result.warnings, UNCONFIRMED_ANSWER))
    return result


class A2AAdapter:
    def __init__(self, config: A2AConfig) -> None:
        self.config = config
        self.headers = {key: value for key, value in config.headers.items() if value is not None}

    async def _card(self) -> wire.AgentCard:
        async with _http_client(self.headers) as http:
            try:
                data, _ = await _read_bytes(http, self.config.card_url, MAX_CARD_BYTES)
                card = parse_agent_card(json.loads(data))
            except httpx.RequestError:
                raise RemoteAgentError("暂时无法连接 Agent Card 地址", status="unknown") from None
            except (httpx.HTTPStatusError, ParseError, TypeError, ValueError):
                raise RemoteAgentError("读取 Agent Card 失败，请检查地址、鉴权和返回格式") from None
        if not card.name:
            raise RemoteAgentError("Agent Card 未声明名称")
        return card

    @staticmethod
    def _interface(interface: wire.AgentInterface) -> dict[str, str]:
        return {
            "url": interface.url, "protocolBinding": interface.protocol_binding,
            "protocolVersion": interface.protocol_version, "tenant": interface.tenant,
        }

    def _select(self, card: wire.AgentCard, *, require_approval: bool) -> wire.AgentInterface:
        interfaces = [item for item in card.supported_interfaces
                      if item.protocol_binding == "JSONRPC"
                      and re.fullmatch(r"1\.0(?:\.\d+)?", item.protocol_version)]
        approved = self.config.approved_interface
        if approved is not None:
            if not isinstance(approved, dict):
                raise RemoteAgentError("已确认的 A2A 接口配置无效")
            for item in interfaces:
                if self._interface(item) == {"tenant": "", **approved}:
                    _validate_url(item.url)
                    return item
            if require_approval:
                raise RemoteAgentError("Agent Card 接口已变更，请重新检查并确认连接")
        if require_approval:
            raise RemoteAgentError("请先检查并确认 Agent Card 声明的接口")
        if not interfaces:
            raise RemoteAgentError("Agent Card 未声明 A2A 1.0 JSON-RPC 接口")
        _validate_url(interfaces[0].url)
        return interfaces[0]

    async def inspect(self) -> dict:
        card = await self._card()
        interface = self._select(card, require_approval=False)
        return {
            "name": card.name[:240], "description": card.description[:1000],
            "approved_interface": self._interface(interface), "streaming": card.capabilities.streaming,
            "input_modes": list(card.default_input_modes)[:30], "output_modes": list(card.default_output_modes)[:30],
        }

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[tuple[Client, httpx.AsyncClient, str]]:
        card = await self._card()
        interface = self._select(card, require_approval=True)
        selected = wire.AgentCard()
        selected.CopyFrom(card)
        selected.ClearField("supported_interfaces")
        selected.supported_interfaces.append(interface)
        headers = {key: value for key, value in self.headers.items() if key.lower() != "a2a-version"}
        headers["A2A-Version"] = "1.0"
        async with _http_client(headers) as http:
            config = ClientConfig(httpx_client=http, streaming=True, polling=not card.capabilities.streaming)
            async with ClientFactory(config).create(selected) as client:
                yield client, http, interface.url

    async def run(self, state: RemoteState, message: str, attachments: Sequence[InputFile],
                  mode: Operation, checkpoint: Checkpoint, *, message_id: str) -> RemoteResult:
        _check_input(attachments)
        if len(attachments) + bool(message) > 20:
            raise RemoteAgentError("A2A 单次消息最多 20 个内容部分")
        if mode == "answer" and (not state.task_id or not state.question_id):
            raise RemoteAgentError("没有可以补答的远端任务")
        if mode == "resume" and not state.context_id:
            raise RemoteAgentError("没有可以继续的远端会话")
        parts = [wire.Part(text=message)] if message else []
        parts.extend(wire.Part(raw=file.data, filename=file.name, media_type=file.mime) for file in attachments)
        outbound = wire.Message(
            role=wire.Role.ROLE_USER, message_id=message_id, parts=parts,
            task_id=state.task_id if mode == "answer" else "",
            context_id=state.context_id if mode != "start" else "",
        )
        if mode == "answer" and state.question_id and not state.cursor.get("a2a_synthetic_question"):
            outbound.metadata.fields["replyTo"].string_value = state.question_id
        unanswered = state.question_id if mode == "answer" else None
        request = wire.SendMessageRequest(message=outbound)
        request.configuration.history_length = 0
        state = state.model_copy(update={
            "question_id": None, "status": "submitted", "task_id": state.task_id if mode == "answer" else None,
            "context_id": None if mode == "start" else state.context_id,
        })
        async with self._client() as (client, http, endpoint):
            task = wire.Task()
            immediate = None
            observed = False
            try:
                async with asyncio.timeout(OBSERVATION_SECONDS):
                    async with aclosing(client.send_message(request)) as stream:
                        async for response in stream:
                            if response.HasField("message"):
                                immediate = response.message
                                break
                            self._merge_event(task, response)
                            if not task.id:
                                continue
                            updated = self._state(state, task)
                            if not task.HasField("status"):
                                updated = updated.model_copy(update={"status": "working"})
                            state = await _accept_state(state, updated, checkpoint)
                            observed = True
                            if state.status not in ACTIVE:
                                break
                    if immediate is None and not observed:
                        raise A2AClientError("Missing task response")
                    while immediate is None and state.status in ACTIVE:
                        task = await client.get_task(wire.GetTaskRequest(id=task.id, history_length=0))
                        state = await _accept_state(state, self._state(state, task), checkpoint)
                        if state.status in ACTIVE:
                            await asyncio.sleep(0.5)
            except (A2AClientError, TimeoutError, httpx.HTTPError, ParseError, ValueError) as exc:
                if not state.task_id:
                    raise RemoteAgentError("发送结果未知，未收到远端响应", status="unknown") from None
                if observed and isinstance(exc, TimeoutError):
                    return RemoteResult(state, "远端任务仍在执行，可按原任务回查。")
                try:
                    async with asyncio.timeout(RECOVERY_SECONDS):
                        task = await client.get_task(wire.GetTaskRequest(id=state.task_id, history_length=0))
                except wire.TaskNotFoundError:
                    raise RemoteTaskExpired("远端任务不存在或已过期") from None
                except (wire.UnsupportedOperationError, A2AClientError, TimeoutError,
                        httpx.HTTPError, ParseError, ValueError):
                    if observed:
                        return RemoteResult(state, "远端暂时无法查询，可稍后按原任务回查。")
                    raise RemoteAgentError("补答结果未知，可按原任务回查。", status="unknown") from None
                state = await _accept_state(state, self._state(state, task), checkpoint)
                result = await self._result(task, state, http, endpoint)
                return _recovered_result(result, unanswered if not observed else None)
            except wire.TaskNotFoundError:
                raise RemoteTaskExpired("远端任务不存在或已过期") from None
            except wire.ContentTypeNotSupportedError:
                raise RemoteAgentError("远端 A2A 接口不支持本次消息或附件类型") from None
            except (wire.InvalidParamsError, wire.UnsupportedOperationError):
                raise RemoteAgentError("远端拒绝本次消息，请检查任务状态和接口能力") from None
            if immediate is not None:
                return await self._direct_message(immediate, state, checkpoint, http, endpoint)
            return await self._result(task, state, http, endpoint)

    @staticmethod
    def _state(state: RemoteState, task: wire.Task) -> RemoteState:
        status = _wire_status(task.status.state)
        question = None
        cursor = {key: value for key, value in state.cursor.items() if key != "a2a_synthetic_question"}
        if status == "input_required":
            question = task.status.message.message_id
            if not question:
                timestamp = task.status.timestamp.ToJsonString() if task.status.HasField("timestamp") else ""
                question = _question_id(task.id, timestamp + _content_text(task.status.message.parts))
                cursor["a2a_synthetic_question"] = True
        return state.model_copy(update={
            "task_id": task.id, "context_id": task.context_id or state.context_id,
            "question_id": question, "status": status, "cursor": cursor,
        })

    @staticmethod
    def _merge_event(task: wire.Task, event: wire.StreamResponse) -> None:
        if event.HasField("task"):
            task.CopyFrom(event.task)
        elif event.HasField("status_update"):
            update = event.status_update
            if task.id and (update.task_id != task.id or update.context_id != task.context_id):
                raise RemoteAgentError("远端流返回了其他任务的状态", status="unknown")
            task.id = update.task_id
            task.context_id = update.context_id
            task.status.CopyFrom(update.status)
        elif event.HasField("artifact_update"):
            update = event.artifact_update
            if task.id and (update.task_id != task.id or update.context_id != task.context_id):
                raise RemoteAgentError("远端流返回了其他任务的产物", status="unknown")
            task.id = update.task_id
            task.context_id = update.context_id
            current = next((item for item in task.artifacts if item.artifact_id == update.artifact.artifact_id), None)
            if current is None:
                if len(task.artifacts) >= MAX_ARTIFACTS:
                    return
                task.artifacts.append(update.artifact)
            elif not update.append:
                current.CopyFrom(update.artifact)
            else:
                for part in update.artifact.parts:
                    previous = current.parts[-1] if current.parts else None
                    kind = part.WhichOneof("content")
                    if (previous is not None and kind in {"text", "raw"}
                            and previous.WhichOneof("content") == kind
                            and (previous.filename, previous.media_type, previous.metadata) ==
                            (part.filename, part.media_type, part.metadata)):
                        left, right = getattr(previous, kind), getattr(part, kind)
                        limit = MAX_OUTPUT_BYTES if kind == "raw" or part.filename else MAX_TEXT
                        if len(left) + len(right) > limit and limit == MAX_OUTPUT_BYTES:
                            raise RemoteAgentError("远端流的文件产物超过大小限制")
                        setattr(previous, kind, (left + right)[:limit])
                        continue
                    current.parts.append(part)

    async def _direct_message(self, message: wire.Message, state: RemoteState, checkpoint: Checkpoint,
                              http: httpx.AsyncClient, endpoint: str) -> RemoteResult:
        if (state.task_id and message.task_id and state.task_id != message.task_id
                or state.context_id and message.context_id and state.context_id != message.context_id):
            raise RemoteAgentError("远端返回的立即响应与本次调用不一致", status="unknown")
        updated = state.model_copy(update={
            "task_id": message.task_id or state.task_id, "context_id": message.context_id or state.context_id,
            "question_id": None, "status": "completed",
        })
        await checkpoint(updated)
        artifact = wire.Artifact(artifact_id=message.message_id, name="artifact", parts=message.parts)
        files, warnings = await self._files([artifact], message.task_id or message.message_id, http, endpoint)
        return RemoteResult(updated, _content_text(message.parts), files, warnings)

    async def _result(self, task: wire.Task, state: RemoteState, http: httpx.AsyncClient, endpoint: str) -> RemoteResult:
        text = _content_text(task.status.message.parts)
        if state.status != "completed":
            return RemoteResult(state, text)
        text = "\n".join(value for value in (text, *(_content_text(item.parts) for item in task.artifacts))
                         if value)[:MAX_TEXT]
        files, warnings = await self._files(task.artifacts, task.id, http, endpoint)
        return RemoteResult(state, text, files, warnings)

    @staticmethod
    async def _files(artifacts, task_id: str, http: httpx.AsyncClient, endpoint: str):
        files: list[OutputFile] = []
        warnings: list[str] = []
        remaining = MAX_OUTPUT_BYTES
        deadline = asyncio.get_running_loop().time() + ARTIFACT_SECONDS
        for artifact in list(artifacts)[:MAX_ARTIFACTS]:
            for index, part in enumerate(artifact.parts):
                is_file = part.HasField("raw") or part.HasField("url") or bool(part.filename)
                if not is_file or len(files) + len(warnings) >= MAX_ARTIFACTS:
                    continue
                name = _name(part.filename or artifact.name or f"artifact-{index + 1}")
                try:
                    if part.HasField("raw"):
                        data, mime = part.raw, part.media_type
                    elif part.HasField("url"):
                        async with asyncio.timeout_at(deadline):
                            if asyncio.get_running_loop().time() >= deadline:
                                raise TimeoutError
                            data, mime = await _download(http, endpoint, part.url, remaining)
                    elif part.HasField("text") or part.HasField("data"):
                        text = part.text if part.HasField("text") else _text_value(MessageToDict(part.data))
                        data, mime = text.encode(), "text/plain" if part.HasField("text") else "application/json"
                    else:
                        raise RemoteAgentError("远端产物缺少内容")
                    if len(data) > remaining:
                        raise RemoteAgentError("文件产物超过大小限制")
                except (httpx.HTTPError, RemoteAgentError, TimeoutError):
                    warnings.append(f"未能获取产物 {name}，可以回查原任务重试。")
                    continue
                remaining -= len(data)
                artifact_id = artifact.artifact_id or hashlib.sha256(artifact.SerializeToString()).hexdigest()[:32]
                files.append(OutputFile(f"{task_id}:{artifact_id}:{index}", name, _mime(part.media_type or mime), data))
        return tuple(files), tuple(warnings)

    async def refresh(self, state: RemoteState, checkpoint: Checkpoint) -> RemoteResult:
        if not state.task_id:
            raise RemoteAgentError("没有可查询的远端任务标识", status="unknown")
        async with self._client() as (client, http, endpoint):
            try:
                task = await client.get_task(wire.GetTaskRequest(id=state.task_id, history_length=0))
            except wire.TaskNotFoundError:
                raise RemoteTaskExpired("远端任务不存在或已过期") from None
            except (wire.UnsupportedOperationError, A2AClientError, httpx.HTTPError, ParseError, ValueError):
                raise RemoteAgentError("暂时无法查询远端任务", status="unknown") from None
            state = await _accept_state(state, self._state(state, task), checkpoint)
            return await self._result(task, state, http, endpoint)

    async def cancel(self, state: RemoteState) -> RemoteResult:
        if not state.task_id:
            raise RemoteAgentError("没有可取消的远端任务标识", status="unknown")
        async with self._client() as (client, _, _):
            try:
                task = await client.cancel_task(wire.CancelTaskRequest(id=state.task_id))
            except wire.TaskNotFoundError:
                raise RemoteTaskExpired("远端任务不存在或已过期") from None
            except (wire.TaskNotCancelableError, wire.UnsupportedOperationError, A2AClientError,
                    httpx.HTTPError, ParseError, ValueError):
                raise RemoteAgentError("远端尚未确认取消", status="unknown") from None
            result = self._state(state, task)
            if (result.task_id != state.task_id or result.status != "canceled"
                    or (state.context_id and result.context_id != state.context_id)):
                raise RemoteAgentError("远端尚未确认取消", status="unknown")
            return RemoteResult(result)
