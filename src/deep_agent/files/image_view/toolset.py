from __future__ import annotations

import asyncio
import hashlib
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter, ValidationError
from pydantic_ai import FunctionToolset, ModelRetry, RunContext, Tool, ToolFailed
from pydantic_ai.messages import BinaryContent, ToolReturn
from pydantic_ai.toolsets import PreparedToolset

from deep_agent.agent.deps import AgentDeps
from deep_agent.files.public_download import load_public_file
from deep_agent.files.schema import AttachmentFileRef, SkillFileRef
from deep_agent.files.storage import FileStoreSizeLimitError, PlatformFileStore
from deep_agent.files.toolset import _safe_skill_file, load_file_bytes
from deep_agent.skills.package import MAX_MODEL_TEXT_BYTES, is_env_resource
from deep_agent.skills.runtime import ResolvedSkill

from .limits import (
    MAX_CALL_IMAGES, MAX_CALL_OUTPUT_BYTES, MAX_CONTEXT_IMAGE_BYTES,
    MAX_IMAGE_SOURCE_BYTES, MAX_PDF_SOURCE_BYTES, MAX_SOURCE_TOTAL_BYTES,
    TOOL_TIMEOUT_SECONDS,
)
from .rendering import detect_format, render_source
from .runtime import _drain, state_for
from .schema import (
    LocalViewImageItems, PublicUrlRef, ViewImageItems,
)

_LOCAL_ADAPTER = TypeAdapter(LocalViewImageItems)
_PUBLIC_ADAPTER = TypeAdapter(ViewImageItems)
_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".pdf"}
_RENDER_ERRORS = {
    "unsupported_format": "此格式暂不支持查看，请使用PNG、JPEG、WebP、GIF或PDF",
    "invalid_source": "文件内容无效，请重新上传后查看",
    "invalid_image": "图片损坏或无法解码，请核对原文件",
    "invalid_pdf": "PDF损坏或无法读取，请核对原文件",
    "encrypted_pdf": "暂不支持加密PDF，请提供解密后的文件",
    "unsupported_pdf_form": "此PDF包含暂不支持的动态表单，请提供普通PDF",
    "page_out_of_range": "物理页码超出PDF总页数，请核对页码",
    "invalid_page_dimensions": "此PDF页面尺寸无效，无法准备预览",
    "pixel_limit": "图片像素超过6400万上限，请缩小后查看",
    "source_size_limit": "文件超过查看工具的大小上限，请拆分或缩小后查看",
    "output_size_limit": "此图片准备后仍过大，请缩小原文件后查看",
    "render_timeout": "此文件准备超过20秒，已停止处理；请减少PDF页数或缩小文件",
    "render_queue_full": "图片处理暂时繁忙，请稍后重试",
    "worker_unavailable": "图片处理进程不可用，请联系管理员检查运行环境",
    "memory_limit": "图片处理达到内存上限，请缩小文件后查看",
    "page_render_failed": "此PDF页面无法渲染，请核对原文件",
}


def _error_fields(error):
    code = error if isinstance(error, str) else getattr(error, "code", None)
    if isinstance(code, str):
        return {"error": _RENDER_ERRORS.get(code, "图片或PDF页面无法准备，请核对原文件"), "error_code": code}
    if isinstance(error, (ModelRetry, ToolFailed)):
        return {"error": str(error)[:500]}
    return {"error": "图片或PDF页面无法准备，请核对原文件", "error_code": "invalid_source"}


@dataclass
class _Request:
    resource: Any
    key: tuple
    pages: list[int] = field(default_factory=list)
    explicit_pages: bool = False


def _resource_key(resource):
    if isinstance(resource, AttachmentFileRef):
        return ("attachment", resource.file_id)
    if isinstance(resource, SkillFileRef):
        return ("skill", resource.skill_code, resource.path)
    return ("url", resource.url)


def _requests(items):
    by_source = {}
    order = []
    seen = set()
    for item in items:
        key = _resource_key(item.resource)
        request = by_source.setdefault(key, _Request(item.resource, key))
        request.explicit_pages |= item.pages is not None
        for page in item.pages or [1]:
            slot = (key, page)
            if slot in seen:
                continue
            seen.add(slot)
            request.pages.append(page)
            order.append(slot)
    if len(order) > MAX_CALL_IMAGES:
        raise ModelRetry(f"一次最多查看 {MAX_CALL_IMAGES} 张图片或PDF页，请减少页码")
    return by_source, order


def _is_skill_image(entry):
    path = str(entry.get("path") or "")
    name = Path(path).name.lower()
    suffix = Path(path).suffix.lower()
    mime = str(entry.get("mime") or "").split(";", 1)[0].lower()
    return (entry.get("category") != "script" and not is_env_resource(path)
            and not name.startswith(".env") and suffix not in {".py", ".pyc", ".pyo"}
            and (suffix in _IMAGE_SUFFIXES
                 or mime.startswith("image/") or mime == "application/pdf"))


def _headers(ctx):
    headers = dict(ctx.deps.request_headers)
    if ctx.deps.user:
        headers.setdefault("user", ctx.deps.user)
    return headers


def _verify_skill_version(item):
    if item.definition_hash is None:
        raise ToolFailed("Skill 定义不可用，请重新加载后查看")
    try:
        raw = _read_skill_bytes(item.package_root, "SKILL.md", MAX_MODEL_TEXT_BYTES)
    except OSError as exc:
        raise ToolFailed("Skill 定义不可用，请重新加载后查看") from exc
    if hashlib.sha256(raw).hexdigest() != item.definition_hash:
        raise ToolFailed("Skill 在本轮解析后发生更新，请重新加载后查看")


async def _thread_read(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # Python cannot stop a running file-read thread. Join the bounded
        # operation before its tool waiter and Run release their resources.
        await _drain(task)
        raise


def _read_skill_bytes(root: Path, relative: str, max_bytes: int) -> bytes:
    """Bound the actual FD read and reject links/replacement, including parents."""
    parts = Path(relative).parts
    if not parts or Path(relative).is_absolute() or any(part in {".", ".."} for part in parts):
        raise ToolFailed("Skill 资源路径无效")
    handles = []
    try:
        root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        handles.append(root_fd)
        parent_fd = root_fd
        for part in parts[:-1]:
            parent_fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent_fd)
            handles.append(parent_fd)
        file_fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent_fd)
        handles.append(file_fd)
        before = os.fstat(file_fd)
        if not stat.S_ISREG(before.st_mode):
            raise ToolFailed("Skill 资源不是普通文件")
        if before.st_size > max_bytes:
            raise FileStoreSizeLimitError("Skill file exceeds bounded read limit")
        with os.fdopen(file_fd, "rb", closefd=False) as handle:
            data = handle.read(max_bytes + 1)
        if len(data) > max_bytes:
            raise FileStoreSizeLimitError("Skill file exceeds bounded read limit")
        after = os.fstat(file_fd)
        current = os.stat(root / relative, follow_symlinks=False)
        root_current, root_opened = os.stat(root, follow_symlinks=False), os.fstat(root_fd)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if (identity(before) != identity(after) or identity(after) != identity(current)
                or not stat.S_ISREG(current.st_mode)
                or (root_current.st_dev, root_current.st_ino) != (root_opened.st_dev, root_opened.st_ino)
                or not (root / relative).resolve(strict=True).is_relative_to(root.resolve(strict=True))):
            raise ToolFailed("Skill 资源在读取期间发生替换，请重新加载后查看")
        return data
    except FileStoreSizeLimitError:
        raise
    except OSError as exc:
        raise ToolFailed("Skill 资源读取失败或路径发生替换，请重新加载后查看") from exc
    finally:
        for descriptor in reversed(handles):
            os.close(descriptor)


async def _download_file(file_id, headers, *, file_store=None, max_bytes=MAX_PDF_SOURCE_BYTES):
    return await (file_store or PlatformFileStore()).download_bounded(file_id, headers, max_bytes=max_bytes)


async def _bounded_bytes(ctx, *, key, loader, expected_hash, limit, label):
    if limit <= 0:
        raise _BatchLimit("一次来源文件合计超过 200 MB，请拆分查看")
    if key in ctx.deps.file_bytes:
        if len(ctx.deps.file_bytes[key]) > limit:
            raise _BatchLimit("一次来源文件合计超过 200 MB，请拆分查看")
        task_key = key
    else:
        # Never join another tool's possibly unbounded in-flight download.
        task_key = f"view-image:{key}:{limit}"

    async def checked_loader():
        data = await loader()
        if len(data) > limit:
            raise FileStoreSizeLimitError("File exceeds bounded source limit")
        return data

    try:
        data = await load_file_bytes(ctx, key=task_key, loader=checked_loader, failure_label=label,
                                     max_bytes=limit, expected_hash=expected_hash, tool_name="view_image")
    except ToolFailed as exc:
        cause = exc
        for _ in range(8):
            if cause is None:
                break
            if isinstance(cause, FileStoreSizeLimitError):
                raise _BatchLimit("一次来源文件合计超过 200 MB，请拆分查看") from exc
            cause = cause.__cause__
        raise
    ctx.deps.file_bytes.setdefault(key, data)
    return data


async def _source(ctx, request, resources, *, web_authorized, limit):
    """Recheck authority on every call, before using any Run cache."""
    resource = request.resource
    if isinstance(resource, PublicUrlRef):
        if not web_authorized:
            raise ModelRetry("当前 Agent 未授权网络工具，不能查看公网 URL")
        state = state_for(ctx)
        value = await state.cached(
            ("url", resource.url), lambda: load_public_file(resource.url),
            size_of=lambda value: len(value[0]), max_cache_bytes=MAX_IMAGE_SOURCE_BYTES,
        )
        data, name, _mime, final_url = value
        return data, name, {"type": "url", "url": final_url}
    if isinstance(resource, SkillFileRef):
        item, entry, path = _safe_skill_file(resources, resource, tool_name="view_image")
        if not _is_skill_image(entry):
            raise ModelRetry("只能查看 Skill 中已索引的图片或PDF，不能读取脚本、ENV或其他配置")
        expected = entry.get("content_hash")
        if not isinstance(expected, str) or not expected:
            raise ToolFailed("Skill 资源缺少版本指纹，请重新加载后查看")
        await _thread_read(_verify_skill_version, item)

        async def read():
            return await _thread_read(_read_skill_bytes, item.package_root, resource.path, limit)

        data = await _bounded_bytes(
            ctx, key=f"skill:{resource.skill_code}:{resource.path}:{expected}",
            loader=read, label="Skill 图片", limit=limit, expected_hash=expected,
        )
        await _thread_read(_verify_skill_version, item)
        return data, Path(resource.path).name, resource.model_dump()

    attachment = ctx.deps.attachment_by_id.get(resource.file_id)
    artifact = ctx.deps.session_file_artifacts.get(resource.file_id)
    if artifact is None:
        artifact = ctx.deps.tool_output_artifacts.get(resource.file_id)
    if attachment is None and artifact is None:
        raise ModelRetry("file_id 不属于当前会话附件或可信工具生成文件")
    if attachment is not None:
        name, key, expected = attachment.name or attachment.id, f"attachment:{attachment.id}", None
    else:
        name = str(artifact.get("file_name") or resource.file_id)
        prefix = "tool-result" if artifact.get("kind") == "tool_result_overflow" else "session-file"
        key = f"{prefix}:{resource.file_id}"
        expected = artifact.get("sha256") or artifact.get("content_hash")
        size = artifact.get("byte_size")
        if isinstance(size, int) and size > limit:
            raise _BatchLimit("一次来源文件合计超过 200 MB，请拆分查看")
    data = await _bounded_bytes(
        ctx, key=key,
        loader=lambda: _download_file(resource.file_id, _headers(ctx), file_store=ctx.deps.file_store, max_bytes=limit),
        label="图片或PDF", limit=limit, expected_hash=expected,
    )
    return data, name, resource.model_dump()


async def _execute(ctx, items, resources, *, web_authorized):
    try:
        parsed = (_PUBLIC_ADAPTER if web_authorized else _LOCAL_ADAPTER).validate_python(items)
    except ValidationError as exc:
        raise ModelRetry("items 需包含 1～8 个已授权来源；PDF页码必须为正整数，图片不要传 pages，URL需授权网络工具") from exc
    requests, order = _requests(parsed)
    state = state_for(ctx)
    token = await state.reserve(ctx, len(order))
    try:
        prepared = {}
        errors = {}
        source_bytes = 0
        # Download and validate the whole batch before rendering. A global
        # source-size failure never silently drops a later file.
        # All public sources together are at most 8*20 MB. Prepare those first
        # so remaining local reads can use a strict shared 200 MB byte budget.
        source_order = sorted(requests.items(), key=lambda pair: not isinstance(pair[1].resource, PublicUrlRef))
        for key, request in source_order:
            try:
                data, name, resource = await _source(ctx, request, resources, web_authorized=web_authorized,
                                                     limit=min(MAX_PDF_SOURCE_BYTES, MAX_SOURCE_TOTAL_BYTES - source_bytes))
                source_bytes += len(data)
                if source_bytes > MAX_SOURCE_TOTAL_BYTES:
                    raise _BatchLimit("一次来源文件合计超过 200 MB，请拆分查看")
                kind = detect_format(data)
                if kind != "pdf" and len(data) > MAX_IMAGE_SOURCE_BYTES:
                    raise ToolFailed("原始图片超过 20 MB 上限")
                if kind != "pdf" and request.explicit_pages:
                    raise ModelRetry("图片不能使用 pages；只有PDF可以选择物理页码")
                prepared[key] = (data, name, resource, kind)
            except _BatchLimit:
                raise
            except (ModelRetry, ToolFailed, ValueError) as exc:
                errors[key] = _error_fields(exc)

        results = {}
        binaries = {}
        output_bytes = 0
        for key, request in requests.items():
            original_resource = request.resource.model_dump()
            if key in errors:
                for page in request.pages:
                    results[(key, page)] = {"resource": original_resource, "name": _name(request.resource),
                                             "format": None, "page": page if request.explicit_pages else None,
                                             "status": "error", **errors[key]}
                continue
            data, name, resource, kind = prepared[key]
            pages = request.pages if kind == "pdf" else None
            digest = hashlib.sha256(data).hexdigest()
            try:
                rendered = await state.cached(
                    ("render", digest, tuple(pages) if pages else None),
                    lambda: render_source(data, pages),
                    size_of=lambda result: sum(len(image["data"]) for image in result["images"]),
                    max_cache_bytes=MAX_CONTEXT_IMAGE_BYTES,
                )
            except ValueError as exc:
                for page in request.pages:
                    results[(key, page)] = {"resource": resource, "name": name, "format": kind,
                                             "page": page if kind == "pdf" else None,
                                             "status": "error", **_error_fields(exc)}
                continue
            for image in rendered["images"]:
                slot = (key, image["page"] if kind == "pdf" else 1)
                output_bytes += len(image["data"])
                if output_bytes > MAX_CALL_OUTPUT_BYTES:
                    raise _BatchLimit("一次准备的图片超过 16 MB，请减少图片或PDF页数")
                results[slot] = {
                    "resource": resource, "name": name, "format": rendered["format"],
                    "page_count": rendered["page_count"], "status": "ready",
                    **{k: value for k, value in image.items() if k not in {"data", "media_type"}},
                    "media_type": image["media_type"], "bytes": len(image["data"]),
                }
                binaries[slot] = BinaryContent(data=image["data"], media_type=image["media_type"])
            for error in rendered["errors"]:
                page = error.get("page")
                results[(key, page if kind == "pdf" else 1)] = {
                    "resource": resource, "name": name, "format": kind, "page": page,
                    "page_count": rendered["page_count"], "status": "error", **_error_fields(error["error"]),
                }
            # A missing renderer page is a failure, never an implicit success.
            for page in request.pages:
                results.setdefault((key, page), {"resource": resource, "name": name, "format": kind,
                                                "page": page if kind == "pdf" else None, "status": "error",
                                                "error": "未能准备此页，请核对文件和物理页码"})
        ordered = [results[slot] for slot in order]
        images = [binaries[slot] for slot in order if slot in binaries]
        if not images:
            raise ToolFailed("未能查看任何图片或PDF页：" + "；".join(str(item["error"]) for item in ordered)[:2000])
        await state.deliver(ctx, token, images)
        safe = {"kind": "view-image-result", "status": "ready" if len(images) == len(order) else "partial", "items": ordered}
        content = [safe]
        for slot in order:
            if slot in binaries:
                item = results[slot]
                label = f"图片来源：{item['name']}"
                if item.get("page") is not None:
                    label += f" · PDF物理第 {item['page']} 页"
                if item.get("gif_first_frame"):
                    label += " · GIF仅查看首帧"
                content.extend([label, binaries[slot]])
        return ToolReturn(return_value=content, metadata={"view_image": safe, "web_content_untrusted": True})
    except _BatchLimit as exc:
        raise ModelRetry(str(exc)) from exc
    finally:
        state.pending.pop(token, None)


class _BatchLimit(ValueError):
    pass


def _name(resource):
    if isinstance(resource, AttachmentFileRef):
        return resource.file_id
    if isinstance(resource, SkillFileRef):
        return Path(resource.path).name
    return resource.url


def build_view_image_toolset(resources, *, web_authorized, python_available):
    authorized = dict(resources)

    async def run(ctx, items):
        try:
            async with asyncio.timeout(TOOL_TIMEOUT_SECONDS):
                return await _execute(ctx, items, authorized, web_authorized=web_authorized)
        except TimeoutError as exc:
            raise ToolFailed("查看图片超过 60 秒，已停止本次处理；请减少文件或PDF页数") from exc

    # Different signatures ensure unprivileged schemas never advertise a URL
    # branch. The execution adapter independently checks the same permission.
    if web_authorized:
        async def view_image(ctx: RunContext[AgentDeps], items: ViewImageItems) -> ToolReturn:
            return await run(ctx, items)
    else:
        async def view_image(ctx: RunContext[AgentDeps], items: LocalViewImageItems) -> ToolReturn:
            return await run(ctx, items)

    toolset = FunctionToolset[AgentDeps](
        id="image-view",
        tools=[Tool(view_image, timeout=TOOL_TIMEOUT_SECONDS, description=(
            "用当前主模型查看已授权图片或PDF原页。可一次比较多个来源，合计最多8张图片/PDF页；"
            "PDF pages 是从1开始的物理页，省略只看第1页；图片不要传pages。"
            "支持当前会话附件与可信工具产物的file_id、已选Skill图片/PDF"
            + ("、已授权公网HTTP(S) URL" if web_authorized else "") + "。"
            "文字读取使用read_file，专业解析使用已提供的parse_document。"
            "模糊或不可见的内容需明确不确定，不要猜测。此工具只在外层调用，不能在run_code中使用。"
            "PTC/Python/Skill先生成并上传文件、取得真实file_id，再调用本工具查看。"
            "图片会在本轮后续模型请求中累积提供，下一轮需要原图时重新查看。"
        ))],
    )

    async def prepare(ctx, tools):
        if (web_authorized or python_available or ctx.deps.attachment_by_id
                or ctx.deps.session_file_artifacts or ctx.deps.tool_output_artifacts
                or any(_is_skill_image(entry) for item in authorized.values() for entry in item.resource_index)):
            return tools
        return {}

    return PreparedToolset(toolset, prepare)
