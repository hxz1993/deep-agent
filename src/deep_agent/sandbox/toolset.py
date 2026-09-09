from __future__ import annotations

import asyncio
import hashlib
import mimetypes
import re
import uuid
from pathlib import PurePosixPath
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic_ai import FunctionToolset, ModelRetry, RunContext, ToolFailed, ToolReturn

from deep_agent.agent.attachments import _download_file
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.media_pipeline import max_upload_bytes
from deep_agent.capabilities.registry import ToolInfo, ToolPackage
from deep_agent.files.storage import FileStore, PlatformFileStore
from deep_agent.options import OPTION
from deep_agent.sandbox.backend import PythonExecutionBackend
from deep_agent.sandbox.profile import (
    ResolvedSandboxProfile,
    format_profile_tool_instructions,
    format_python_tool_instructions,
)
from deep_agent.sandbox.types import ExecuteRequest, JsonValue, SandboxInputFile
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT

__all__ = ["build_python_sandbox_tool_package"]

_FALLBACK_INSTRUCTIONS = format_python_tool_instructions(
    (
        "numpy",
        "pandas",
        "pydantic",
        "requests",
        "httpx",
        "Pillow",
        "matplotlib",
        "pypdf",
        "openpyxl",
        "python-docx",
        "python-pptx",
    )
)

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
            data = await _download_file(
                file_id,
                _request_headers(ctx),
                file_store=_file_store(ctx),
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise ToolFailed("当前会话文件读取失败") from exc
        ctx.deps.file_bytes[cache_key] = data
    if len(data) > max_upload_bytes():
        raise ModelRetry(
            f"输入文件超过当前 {max_upload_bytes()} 字节上传上限"
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
) -> None:
    for file_id in reversed(file_ids):
        try:
            await store.delete(file_id, headers)
        except Exception:
            pass


def build_python_sandbox_tool_package(
    backend: PythonExecutionBackend,
    *,
    timeout_sec: float,
    sandbox_profile: ResolvedSandboxProfile | None = None,
) -> ToolPackage:
    timeout_ms = int(timeout_sec * 1000)

    async def execute_python(
        ctx: RunContext[AgentDeps],
        code: str,
        inputs: dict[str, JsonValue] | None = None,
        files: list[SandboxFileInput] | None = None,
    ) -> ToolReturn:
        """执行 Python 计算、解析或文件转换，可读取会话文件并发布转换产物。

        公网文件无需修改、只需原样传给 MCP 时，不要用本工具重复抓取或探测网络；
        应在 PTC 中使用 load_file_data(url=...) 直接完成读取、上传和发送依赖链。
        只有确实需要解析、转码或生成新文件时才使用 Python 沙箱。
        用户仅要求发送/转交 WebP 等公网文件时不属于转码需求，保持原文件中转即可；
        只有用户明确要求 PNG/JPEG 或内联图片时才尝试转换。
        """
        if ctx.deps.user is None:
            raise ModelRetry("python-sandbox 需要已认证用户，无法在无用户身份时执行")

        requested_files = [
            item if isinstance(item, SandboxFileInput) else SandboxFileInput.model_validate(item)
            for item in (files or [])
        ]
        if len(requested_files) > int(OPTION.agent.upload_max_count):
            raise ModelRetry(
                f"files 最多允许 {OPTION.agent.upload_max_count} 个文件"
            )
        ids = [item.file_id.strip() for item in requested_files]
        if len(ids) != len(set(ids)):
            raise ModelRetry("files.file_id 不能重复")
        sandbox_files = tuple(
            [
                await _authorized_input(ctx, item, index=index)
                for index, item in enumerate(requested_files, start=1)
            ]
        )

        request = ExecuteRequest(
            request_id=uuid.uuid4().hex,
            code=code,
            inputs=inputs or {},
            timeout_ms=timeout_ms,
            files=sandbox_files,
        )
        result = await backend.execute(request)
        sandbox_paths = tuple(
            [item.path for item in sandbox_files]
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

        if len(result.published_files) > int(OPTION.agent.upload_max_count):
            raise ModelRetry(
                f"publish_path 最多允许 {OPTION.agent.upload_max_count} 个输出文件"
            )
        for output in result.published_files:
            if output.byte_size > max_upload_bytes():
                raise ModelRetry(
                    f"生成文件 {output.file_name} 超过当前 {max_upload_bytes()} 字节上传上限"
                )

        store = _file_store(ctx)
        headers = _request_headers(ctx)
        uploaded_ids: list[str] = []
        refs: list[dict[str, object]] = []
        try:
            for output in result.published_files:
                mime = output.mime or mimetypes.guess_type(output.file_name)[0]
                mime = mime or "application/octet-stream"
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
            await _rollback_uploads(store, uploaded_ids, headers)
            raise
        except Exception as exc:
            await _rollback_uploads(store, uploaded_ids, headers)
            raise ToolFailed("Python 生成文件上传失败，已回滚本次产物") from exc

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

    instructions = (
        format_profile_tool_instructions(sandbox_profile)
        if sandbox_profile is not None
        else _FALLBACK_INSTRUCTIONS
    )

    toolset = FunctionToolset(
        instructions=instructions,
        tools=[execute_python],
    )

    return ToolPackage(
        code="builtin:sandbox",
        category="builtin",
        toolset=toolset,
        label="Python 沙箱",
        source="builtin",
        description="在无状态 Python 环境中执行数据计算。",
        tools=[
            ToolInfo(
                "execute_python",
                "执行 Python",
                "执行无状态 Python 计算；可读取当前会话文件、访问公网并发布转换产物到 FileStore。",
            ),
        ],
    )
