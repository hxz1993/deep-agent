from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic_ai import ModelRetry, RunContext, ToolFailed, ToolReturn

from deep_agent.agent.attachments import _download_file
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.media_pipeline import max_upload_bytes
from deep_agent.agent.tool_errors import observe_tool_operation, tool_error_message
from deep_agent.files.storage import FileStore, PlatformFileStore
from deep_agent.files.upload_settings import UploadSettings
from deep_agent.sandbox.types import ExecuteResult, SandboxInputFile
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT

_HOST_TEMP_PATH = re.compile(
    r"(?<![A-Za-z0-9:])/(?:private/)?(?:var/folders|var/tmp|tmp|Users)/[^\s\"']+"
)


class SandboxFileInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    file_id: str
    path: str | None = None


def _request_headers(ctx: RunContext[AgentDeps]) -> dict[str, str]:
    headers = dict(ctx.deps.request_headers)
    if ctx.deps.user:
        headers.setdefault("user", ctx.deps.user)
    return headers


def _file_store(ctx: RunContext[AgentDeps]) -> FileStore:
    return ctx.deps.file_store or PlatformFileStore()


def _artifact_map(ctx: RunContext[AgentDeps]) -> dict[str, dict[str, Any]]:
    session_artifacts = getattr(ctx.deps, "session_file_artifacts", None)
    if isinstance(session_artifacts, dict):
        return session_artifacts
    return ctx.deps.tool_output_artifacts


def _scrub_sandbox_paths(value: Any, *, exact_paths: tuple[str, ...]) -> Any:
    if isinstance(value, str):
        scrubbed = value
        for path in exact_paths:
            if path:
                scrubbed = scrubbed.replace(path, "[sandbox-path]")
        return _HOST_TEMP_PATH.sub("[sandbox-path]", scrubbed)
    if isinstance(value, list):
        return [_scrub_sandbox_paths(item, exact_paths=exact_paths) for item in value]
    if isinstance(value, tuple):
        return [_scrub_sandbox_paths(item, exact_paths=exact_paths) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _scrub_sandbox_paths(item, exact_paths=exact_paths)
            for key, item in value.items()
        }
    return value


async def _authorized_input(
    ctx: RunContext[AgentDeps],
    item: SandboxFileInput,
    *,
    index: int,
    tool_name: str,
) -> SandboxInputFile:
    file_id = item.file_id.strip()
    if not file_id:
        raise ModelRetry("files.file_id 不能为空")
    attachment = ctx.deps.attachment_by_id.get(file_id)
    artifact = _artifact_map(ctx).get(file_id)
    if attachment is None and artifact is None:
        # Compatibility while older runners populate only the overflow map.
        artifact = ctx.deps.tool_output_artifacts.get(file_id)
    if attachment is None and artifact is None:
        raise ModelRetry("file_id 不属于当前会话可读取文件")

    if attachment is not None:
        file_name = attachment.name or f"input-{index}"
        cache_key = f"attachment:{file_id}"
    else:
        assert artifact is not None
        file_name = str(artifact.get("file_name") or f"input-{index}")
        cache_key = f"session-file:{file_id}"

    data = ctx.deps.file_bytes.get(cache_key)
    if data is None:
        try:
            async with observe_tool_operation(ctx, "download", tool=tool_name):
                data = await _download_file(
                    file_id,
                    _request_headers(ctx),
                    file_store=_file_store(ctx),
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ToolFailed(tool_error_message("当前会话文件读取失败", exc, _request_headers(ctx))) from exc
        ctx.deps.file_bytes[cache_key] = data
    if len(data) > max_upload_bytes():
        raise ModelRetry(
            f"输入文件超过 {max_upload_bytes()} 字节读取安全上限"
        )

    path = item.path
    if path is None:
        base_name = PurePosixPath(file_name.replace("\\", "/")).name
        path = f"inputs/{index}-{base_name or 'file.bin'}"
    try:
        return SandboxInputFile.from_bytes(path, data, file_id=file_id)
    except ValueError as exc:
        raise ModelRetry(f"沙箱输入路径无效: {path}") from exc


async def _rollback_uploads(
    store: FileStore,
    file_ids: list[str],
    headers: dict[str, str],
    ctx: RunContext[AgentDeps],
    *, tool_name: str,
) -> list[str]:
    failures = []
    for file_id in reversed(file_ids):
        try:
            async with observe_tool_operation(ctx, "rollback_upload", tool=tool_name):
                await store.delete(file_id, headers)
        except Exception as exc:
            failures.append(tool_error_message("产物清理失败", exc, headers))
    return failures


async def prepare_input_files(ctx: RunContext[AgentDeps], files: list[SandboxFileInput] | None,
                              settings: UploadSettings, *, tool_name: str,
                              reserve_skill: bool = False) -> tuple[SandboxInputFile, ...]:
    requested = [item if isinstance(item, SandboxFileInput) else SandboxFileInput.model_validate(item)
                 for item in (files or [])]
    if len(requested) > settings.max_count:
        raise ModelRetry(f"files 最多允许 {settings.max_count} 个文件")
    ids = [item.file_id.strip() for item in requested]
    if len(ids) != len(set(ids)):
        raise ModelRetry("files.file_id 不能重复")
    if reserve_skill:
        for item in requested:
            parts = PurePosixPath(item.path.replace("\\", "/").strip()).parts if item.path else ()
            if parts and parts[0].casefold() in {
                "skill", "outputs", "inputs.json", "main.py", ".deepagent-files.json",
            }:
                raise ModelRetry("附件路径不能覆盖 Skill 执行副本")
    return tuple([await _authorized_input(ctx, item, index=index, tool_name=tool_name)
                  for index, item in enumerate(requested, start=1)])


async def publish_execution_result(ctx: RunContext[AgentDeps], result: ExecuteResult,
                                   settings: UploadSettings, *, tool_name: str,
                                   input_files: tuple[SandboxInputFile, ...] = ()) -> ToolReturn:
    sandbox_paths = tuple(
        [item.path for item in input_files]
        + [f"outputs/{item.file_name}" for item in result.published_files]
    )
    public_result = _scrub_sandbox_paths(
        result.model_dump(mode="json"),
        exact_paths=sandbox_paths,
    )
    public_result["files"] = []
    if result.status != "ok" or not result.published_files:
        return ToolReturn(
            return_value=public_result,
            metadata={"generated_files": []},
        )

    if len(result.published_files) > settings.max_count:
        raise ModelRetry(
            f"publish_path 最多允许 {settings.max_count} 个输出文件"
        )
    for output in result.published_files:
        if output.byte_size > settings.max_bytes:
            raise ModelRetry(
                f"生成文件 {output.file_name} 超过当前 {settings.max_bytes} 字节上传上限"
            )

    store = _file_store(ctx)
    headers = _request_headers(ctx)
    uploaded_ids: list[str] = []
    refs: list[dict[str, object]] = []
    try:
        for output in result.published_files:
            mime = output.mime or mimetypes.guess_type(output.file_name)[0]
            mime = mime or "application/octet-stream"
            async with observe_tool_operation(ctx, "upload", tool=tool_name, byte_size=output.byte_size):
                stored = await store.upload(
                    file_type=FILE_TYPE_ATTACHMENT,
                    file_name=output.file_name,
                    mime=mime,
                    data=output.data,
                    request_headers=headers,
                )
            uploaded_ids.append(stored.id)
            refs.append(
                {
                    "kind": "sandbox_output",
                    "file_id": stored.id,
                    "file_name": stored.file_name or output.file_name,
                    "mime": mime,
                    "byte_size": output.byte_size,
                    "sha256": hashlib.sha256(output.data).hexdigest(),
                }
            )
    except asyncio.CancelledError:
        await _rollback_uploads(store, uploaded_ids, headers, ctx, tool_name=tool_name)
        raise
    except Exception as exc:
        cleanup_failures = await _rollback_uploads(store, uploaded_ids, headers, ctx, tool_name=tool_name)
        message = tool_error_message(f"{tool_name} 生成文件上传失败", exc, headers)
        if cleanup_failures:
            message += "；部分本次产物清理失败，可能仍有残留文件；" + cleanup_failures[0]
        elif uploaded_ids:
            message += "；已清理本次已上传的产物"
        else:
            message += "；本次没有已上传成功的产物"
        raise ToolFailed(message) from exc

    artifacts = getattr(ctx.deps, "session_file_artifacts", None)
    if not isinstance(artifacts, dict):
        artifacts = {}
        setattr(ctx.deps, "session_file_artifacts", artifacts)
    for ref in refs:
        artifacts[str(ref["file_id"])] = dict(ref)

    public_result["files"] = refs
    return ToolReturn(
        return_value=public_result,
        metadata={"generated_files": refs},
    )
