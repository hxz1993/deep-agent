from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from uuid import uuid4

from a2a import types as wire
from fastapi import HTTPException
from google.protobuf import json_format
from google.protobuf.struct_pb2 import Value
from pydantic_ai.exceptions import RunCancelled
from sqlalchemy import select

from deep_agent.agent.headers import filter_verified_mcp_headers
from deep_agent.agent.media_pipeline import MediaPipelineError, process_upload
from deep_agent.agent.input_capabilities import document_upload_mimes
from deep_agent.agent.tool_output_limits import artifacts_from_metadata
from deep_agent.mcp_request_context import resolve_mcp_request_context_mode
from deep_agent.db.models import Agent, ChatSession, McpServer
from deep_agent.db.reference_integrity import lock_reference
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.documents.settings import load_document_parsing_config
from deep_agent.files.upload_settings import load_upload_settings
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.services.integration_app_access import IntegrationAppAccessError, PlatformAppAccessClient
from deep_agent.services.integration_app_runtime import RunAppContext
from deep_agent.integrations.app_scope import app_skill_scope
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT

from .publications import PublicationScope, PublicationStore
from .tasks import AUTH_REQUIRED, CANCELED, FAILED, INPUT_REQUIRED, MAX_ARTIFACTS, ArtifactFile, Entry, Result

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_TEXT_BYTES = 128 * 1024
MAX_OUTPUT_BYTES = 256 * 1024


async def permitted_agent(ctx, agent_id: str) -> list[str]:
    row = await ctx.stores.agent.get_by_id(agent_id)
    if row is None or not row.model_id:
        raise HTTPException(422, "请选择已配置默认模型的 Agent")
    capabilities = row.capabilities or []
    if any(code.startswith("ui:") for code in capabilities):
        raise HTTPException(422, "请选择无需前端 UI 工具的 Agent")
    names = [code[4:] for code in capabilities if code.startswith("mcp:")]
    if names:
        async with SessionBoundMixin(ctx.db_session_factory)._read() as db:
            servers = (await db.scalars(select(McpServer).where(McpServer.name.in_(names)))).all()
        for server in servers:
            config = server.config
            mode = resolve_mcp_request_context_mode(server.transport, config)
            if mode != "inherit" or config.get("headers"):
                raise HTTPException(422, "发布的 Agent 不能使用带独立凭据或固定请求头的 MCP")
    return capabilities


async def prepare_input(ctx, message: wire.Message):
    texts, uploads = [], []
    raw_parts = [part for part in message.parts if part.WhichOneof("content") == "raw"]
    settings = None
    extra_mimes = ()
    if raw_parts:
        settings = await load_upload_settings(ctx.stores.global_setting)
        if len(raw_parts) > settings.max_count or sum(len(part.raw) for part in raw_parts) > MAX_INPUT_BYTES:
            raise wire.ContentTypeNotSupportedError(message="附件超过 A2A 请求上限")
        config = await load_document_parsing_config(ctx.stores.global_setting)
        extra_mimes = document_upload_mimes(config)
    for part in message.parts:
        kind = part.WhichOneof("content")
        if kind == "text":
            texts.append(part.text)
        elif kind == "data":
            texts.append(json.dumps(json_format.MessageToDict(part.data), ensure_ascii=False))
        elif kind == "raw":
            try:
                uploads.append(process_upload(
                    data=part.raw, mime=part.media_type or "application/octet-stream",
                    filename=part.filename or "attachment", additional_mimes=extra_mimes, upload_settings=settings,
                ))
            except MediaPipelineError as exc:
                raise wire.ContentTypeNotSupportedError(message=str(exc)) from exc
        else:
            raise wire.ContentTypeNotSupportedError(message="请提交文本、JSON 或内联文件")
    text = "\n\n".join(texts)
    if (not text.strip() and not uploads) or len(text.encode()) > MAX_TEXT_BYTES:
        raise wire.InvalidParamsError(message="消息为空或超过文本上限")
    return text, uploads


async def _ensure_session(ctx, entry: Entry):
    scope = entry.context
    async with SessionBoundMixin(ctx.db_session_factory)._write() as db:
        await lock_reference(db, Agent, Agent.id, scope.agent_id, "Agent")
        session = await db.get(ChatSession, scope.session_id)
        if session is None:
            db.add(ChatSession(
                session_id=scope.session_id, agent_id=scope.agent_id, user_id=scope.owner.user,
                app_id=scope.owner.app, space_id=scope.owner.space, source_type=scope.source,
                source_id=scope.owner.publication_id, title=entry.title, next_sequence=1,
            ))
        elif (session.agent_id, session.user_id, session.app_id, session.space_id, session.source_type, session.source_id) != (
            scope.agent_id, scope.owner.user, scope.owner.app, scope.owner.space, scope.source, scope.owner.publication_id,
        ):
            raise RuntimeError("A2A session binding changed")


def _file_artifacts(entry: Entry, events, result: Result):
    by_file = {file.id for file in (*entry.files.values(), *result.files.values())}
    for event in events:
        payload = event.payload.get("message") or {}
        parts = payload.get("parts") or []
        for part in parts:
            if part.get("part_kind") != "tool-return" or part.get("tool_name") not in {
                "execute_python", "execute_skill_script", "run_code", "export_file_to_download_center",
            }:
                continue
            metadata = part.get("metadata") or {}
            for item in artifacts_from_metadata(metadata):
                if len(result.artifacts) >= MAX_ARTIFACTS:
                    return
                if item.get("role") != "generated_file":
                    continue
                file_id = item.get("file_id")
                size = item.get("byte_size")
                if not isinstance(file_id, str) or not file_id or file_id in by_file or type(size) is not int or size < 0:
                    continue
                name = PurePath(str(item.get("file_name") or "artifact").replace("\\", "/")).name[:240] or "artifact"
                mime = item.get("mime") or "application/octet-stream"
                if not isinstance(mime, str) or not re.fullmatch(r"[\w.+-]+/[\w.+-]+", mime):
                    mime = "application/octet-stream"
                artifact_id = uuid4().hex
                result.files[artifact_id] = ArtifactFile(file_id, name, mime, size)
                result.artifacts.append(wire.Artifact(artifact_id=artifact_id, name=name, parts=[
                    wire.Part(filename=name, media_type=mime),
                ]))
                by_file.add(file_id)


@dataclass
class RunnerExecution:
    ctx: object
    headers: dict[str, str]
    publication_code: str
    text: str
    uploads: list

    async def __call__(self, entry: Entry, _message: wire.Message, continuing: bool) -> Result:
        ctx, headers = self.ctx, self.headers
        scope = entry.context
        text, uploads = entry.retry_input or (self.text, self.uploads)
        if entry.cancellation_requested:
            return Result(state=CANCELED)
        try:
            await PlatformAppAccessClient(headers).verify_app_access(scope.owner.app, scope.owner.space)
        except IntegrationAppAccessError as exc:
            if exc.status == 401 and not uploads:
                entry.retry_input = (text, uploads)
                return Result(state=AUTH_REQUIRED, question="请更新平台凭据后继续。")
            raise
        if entry.cancellation_requested:
            return Result(state=CANCELED)
        publication = await PublicationStore(ctx.db_session_factory).get(self.publication_code)
        if (publication is None or publication.id != scope.owner.publication_id
                or not PublicationScope.model_validate(publication.scope).allows(scope.owner.app, scope.owner.space)):
            return Result(state=FAILED, question="应用已移出发布范围")
        entry.retry_input = None
        capabilities = await permitted_agent(ctx, scope.agent_id)
        app, space = scope.owner.app, scope.owner.space
        row = await ctx.stores.integration_app.get_for_agent(app, space, scope.agent_id)
        app_context = RunAppContext(app, space) if row is None else RunAppContext(
            app, space, row.settings.get("prompt_append", ""), app_skill_scope(app), tuple(row.settings["skills"]),
        )
        mcp_headers = filter_verified_mcp_headers(headers)
        from deep_agent.api.v1.chat import _agent_deps
        from deep_agent.api.v1.deps import build_runner_for_agent

        runner = await build_runner_for_agent(ctx, scope.agent_id, capabilities=capabilities,
                                               app_context=app_context, request_headers=mcp_headers, a2a=True)
        deps = _agent_deps(ctx, session_id=scope.session_id, user=scope.owner.user, agent_id=scope.agent_id,
                           request_headers=mcp_headers, execution_request_headers=headers, app_context=app_context)
        deps.a2a = True
        if entry.cancellation_requested:
            return Result(state=CANCELED)
        active = ctx.active_runs.register(agent_id=scope.agent_id, session_id=scope.session_id, user_id=scope.owner.user,
                                          conversation_id=deps.conversation_id, run_id=deps.run_id)
        deps.cancellation_token = active.cancellation_token
        entry.stop = active.cancellation_token.cancel
        attachments = []
        started = False
        try:
            await _ensure_session(ctx, entry)
            for upload in uploads:
                stored = await ctx.file_store.upload(
                    file_type=FILE_TYPE_ATTACHMENT, file_name=upload.filename, mime=upload.mime,
                    data=upload.data, request_headers=headers,
                )
                attachments.append(AttachmentRef(id=stored.id, name=stored.file_name, mime=upload.mime))
            if entry.cancellation_requested:
                return Result(state=CANCELED)
            started = True
            if deps.run_id:
                entry.run_ids.append(deps.run_id)
            output = await runner.run(scope.session_id, text, deps, attachments=attachments, run_id=deps.run_id)
        except RunCancelled:
            return Result(state=CANCELED if entry.cancellation_requested else FAILED)
        finally:
            try:
                if not started:
                    await asyncio.gather(*(ctx.file_store.delete(item.id, headers) for item in attachments),
                                         return_exceptions=True)
            finally:
                ctx.active_runs.unregister(active)
        result = Result()
        if output.get("a2a_question"):
            result.state = INPUT_REQUIRED
            result.question = output["a2a_question"]
        elif output.get("status") != "completed":
            return Result(state=FAILED, question="Agent 需要本入口未提供的交互")
        else:
            value = output.get("output")
            encoded = json.dumps(value, ensure_ascii=False).encode()
            if len(encoded) > MAX_OUTPUT_BYTES:
                return Result(state=FAILED, question="Agent 输出超过 A2A 交付上限")
            if isinstance(value, str) and not output.get("output_info"):
                part = wire.Part(text=value, media_type="text/plain")
            else:
                part = wire.Part(data=json_format.ParseDict(value, Value()), media_type="application/json")
            result.artifacts.append(wire.Artifact(artifact_id=uuid4().hex, name="结果", parts=[part]))
        sequence = 0
        while events := await ctx.store.list_events(
            scope.session_id, run_id=deps.run_id, event_type="tool/results", after_sequence=sequence, limit=100,
        ):
            _file_artifacts(entry, events, result)
            sequence = events[-1].sequence
        return result
