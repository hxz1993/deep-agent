from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from pathlib import PurePath
from uuid import uuid4

from a2a import types as wire
from google.protobuf import json_format
from google.protobuf.struct_pb2 import Value
from pydantic_ai.exceptions import RunCancelled

from deep_agent.agent.file_delivery import RunFileDelivery
from deep_agent.agent.headers import filter_verified_mcp_headers
from deep_agent.agent.input_capabilities import document_upload_mimes
from deep_agent.agent.media_pipeline import MediaPipelineError, process_upload
from deep_agent.conversation.models import ConversationOutputFile
from deep_agent.core.exceptions import AgentModelNotConfiguredError, LlmModelNotFoundError
from deep_agent.db.models import Agent, ChatSession
from deep_agent.db.reference_integrity import lock_reference
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.documents.settings import load_document_parsing_config
from deep_agent.files.upload_settings import load_upload_settings
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.services.integration_app_access import IntegrationAppAccessError, PlatformAppAccessClient
from deep_agent.services.integration_app_runtime import RunAppContext
from deep_agent.integrations.app_scope import app_skill_scope
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT

from .output import PublicationProtocol, PublicationResult
from .publications import PublicationScope, PublicationStore
from .tasks import AUTH_REQUIRED, CANCELED, FAILED, INPUT_REQUIRED, ArtifactFile, Entry, Result

MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_TEXT_BYTES = 128 * 1024


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


def _file_artifacts(entry: Entry, files: list[dict], result: Result) -> None:
    registered = {file.id for file in entry.files.values()}
    for item in files:
        file = ConversationOutputFile.model_validate(item)
        if file.id in registered:
            continue
        if type(file.byte_size) is not int or file.byte_size < 0:
            raise ValueError("Presented file has no verified size")
        name = PurePath((file.name or "artifact").replace("\\", "/")).name[:240] or "artifact"
        mime = file.mime or "application/octet-stream"
        if not re.fullmatch(r"[\w.+-]+/[\w.+-]+", mime):
            mime = "application/octet-stream"
        artifact_id = uuid4().hex
        result.files[artifact_id] = ArtifactFile(file.id, name, mime, file.byte_size)
        result.artifacts.append(wire.Artifact(artifact_id=artifact_id, name=name, parts=[
            wire.Part(filename=name, media_type=mime),
        ]))
        registered.add(file.id)


@dataclass
class RunnerExecution:
    ctx: object
    headers: dict[str, str]
    publication_code: str
    text: str
    uploads: list
    protocol: PublicationProtocol

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
                return Result(state=AUTH_REQUIRED, question="请更新平台凭据后继续。",
                              reason="credential_expired", error_type=type(exc).__name__)
            raise
        if entry.cancellation_requested:
            return Result(state=CANCELED)
        publication = await PublicationStore(ctx.db_session_factory).get(self.publication_code)
        if (publication is None or publication.id != scope.owner.publication_id
                or not PublicationScope.model_validate(publication.scope).allows(scope.owner.app, scope.owner.space)):
            return Result(state=FAILED, question="应用已移出发布范围", reason="scope_changed")
        entry.retry_input = None
        app, space = scope.owner.app, scope.owner.space
        row = await ctx.stores.integration_app.get_for_agent(app, space, scope.agent_id)
        app_context = RunAppContext(app, space) if row is None else RunAppContext(
            app, space, row.settings.get("prompt_append", ""), app_skill_scope(app), tuple(row.settings["skills"]),
        )
        mcp_headers = filter_verified_mcp_headers(headers)
        from deep_agent.api.v1.chat import _agent_deps
        from deep_agent.api.v1.deps import build_runner_for_agent

        try:
            runner = await build_runner_for_agent(
                ctx, scope.agent_id, app_context=app_context, request_headers=mcp_headers,
            )
        except AgentModelNotConfiguredError as exc:
            return Result(state=FAILED, question=str(exc), reason="model_not_configured", error_type=type(exc).__name__)
        except LlmModelNotFoundError as exc:
            return Result(state=FAILED, question="Agent 配置的模型不存在", reason="model_not_found", error_type=type(exc).__name__)
        deps = _agent_deps(ctx, session_id=scope.session_id, user=scope.owner.user, agent_id=scope.agent_id,
                           request_headers=mcp_headers, execution_request_headers=headers, app_context=app_context)
        deps.publication_protocol = self.protocol
        deps.file_delivery = RunFileDelivery()
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
                entry.segment_run_id = deps.run_id
                entry.run_ids.append(deps.run_id)
            output = await runner.run(scope.session_id, text, deps, attachments=attachments, run_id=deps.run_id)
        except RunCancelled:
            return Result(state=CANCELED if entry.cancellation_requested else FAILED,
                          reason="execution_canceled", error_type="RunCancelled")
        finally:
            try:
                if not started:
                    await asyncio.gather(*(ctx.file_store.delete(item.id, headers) for item in attachments),
                                         return_exceptions=True)
            finally:
                ctx.active_runs.unregister(active)
        if output.get("status") != "completed":
            return Result(state=FAILED, question="Agent 需要本入口未提供的交互", reason="unsupported_interaction")
        value = PublicationResult.model_validate(output["output"])
        result = Result(question=value.message)
        reply = value.reply
        if value.status == "input_required":
            result.state = INPUT_REQUIRED
            result.status_detail = reply.text if reply else None
        elif reply is not None:
            parts = []
            if reply.text:
                parts.append(wire.Part(text=reply.text, media_type="text/plain"))
            if "data" in reply.model_fields_set:
                parts.append(wire.Part(
                    data=json_format.ParseDict(reply.data, Value()), media_type="application/json",
                ))
            result.artifacts.append(wire.Artifact(artifact_id=uuid4().hex, name="结果", parts=parts))
        _file_artifacts(entry, output.get("output_files", []), result)
        return result
