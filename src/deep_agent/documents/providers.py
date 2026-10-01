"""Official HTTP adapters. Submission is never retried automatically."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import re
import stat
import time
from typing import Any
from urllib.parse import urljoin, urlsplit
import zipfile
from contextlib import asynccontextmanager

import httpx2
from pydantic_ai._ssrf import validate_and_resolve_url, build_url_with_ip

from deep_agent.diagnostics import emit
from deep_agent.web.proxy_compat import install_proxy_compatibility, proxy_compatible_web_fetch
from .models import DocumentPage, DocumentSource, ParsedDocument, ProviderCapability, DocumentParsingError
from .settings import DocumentParsingRuntimeConfig

_PDF_IMAGES = frozenset({"application/pdf", "image/png", "image/jpeg"})
_OFFICE = frozenset({"application/msword", "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
                    "application/vnd.ms-powerpoint", "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    "application/vnd.ms-excel", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"})
PROVIDERS = {
    "mineru": ProviderCapability("mineru", "MinerU", "vlm", (".pdf", ".png", ".jpg", ".jpeg", ".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"),
                                  _PDF_IMAGES | _OFFICE, "精准 VLM；单文件不超过200 MB、200页；服务额度以账号为准", 200_000_000, 200),
    "paddle": ProviderCapability("paddle", "PaddleOCR", "PaddleOCR-VL-1.6", (".pdf", ".png", ".jpg", ".jpeg"),
                                  _PDF_IMAGES, "AI Studio VL-1.6；PDF及PNG/JPEG；文件限制和额度由服务端确认", 200_000_000),
    "glm": ProviderCapability("glm", "GLM-OCR", "glm-ocr", (".pdf", ".png", ".jpg", ".jpeg"),
                               _PDF_IMAGES, "官方 layout_parsing；PDF及PNG/JPEG；文件限制和额度由服务端确认", 200_000_000),
}
_MINERU = "https://mineru.net/api/v4"
_PADDLE = "https://paddleocr.aistudio-app.com/api/v2/ocr/jobs"
_GLM = "https://open.bigmodel.cn/api/paas/v4/layout_parsing"
POLL_SECONDS = 5.0
PARSE_TIMEOUT_SECONDS = 900.0
_MAX_RESULT_BYTES = 200_000_000
_MAX_JSON_BYTES = 64_000_000
_IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,180}\Z")
_RESUMABLE_ERRORS = frozenset({"timeout", "network_error", "rate_limited", "service_error", "artifact_access_denied"})


def can_resume_document(cache_dir: Path, error: BaseException) -> bool:
    """Only resume a confirmed uploaded remote task, never another submission."""
    if not isinstance(error, DocumentParsingError) or error.code not in _RESUMABLE_ERRORS:
        return False
    path = cache_dir / "task.json"
    try:
        if path.stat().st_size > 16_384:
            return False
        value = json.loads(path.read_bytes())
    except (OSError, ValueError, UnicodeError):
        return False
    if not isinstance(value, dict):
        return False
    task_id = value.get("task_id")
    return (
        value.get("provider") in ("mineru", "paddle")
        and type(task_id) is str and bool(_IDENTIFIER.fullmatch(task_id))
        and value.get("uploaded") is True
        and not value.get("submission_unknown") and not value.get("upload_unknown")
    )


def _save(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.write_bytes(value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode())
    path.chmod(0o600)


async def _cpu(function, *args):
    """Join a worker before cancellation propagates so cleanup cannot race writes."""
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if task.done() and not task.cancelled():
            task.exception()  # retrieve a worker failure before propagating cancel
        raise


def _load_task(path: Path, signature: str) -> dict:
    if not path.exists():
        return {"signature": signature}
    value = json.loads(path.read_bytes())
    if value.get("signature") != signature:
        raise DocumentParsingError("cache_conflict", "缓存目录对应其他文件或解析参数，不能复用")
    if value.get("submission_unknown"):
        raise DocumentParsingError("submission_unknown", "提交或上传结果不明确；为避免重复解析，未自动重新提交")
    return value


def _status_error(status: int, *, signed: bool = False) -> DocumentParsingError:
    if signed and status in (401, 403):
        return DocumentParsingError("artifact_access_denied", "供应商上传或下载签名授权已失效，未重新提交解析任务")
    code, message = {
        401: ("invalid_key", "API Key 无效或已过期"),
        403: ("permission_denied", "该 API Key 无权调用文档解析接口"),
        402: ("quota_exceeded", "供应商额度不足"),
        429: ("rate_limited", "供应商限流或额度已用尽，请稍后重试"),
        413: ("file_too_large", "文件超过供应商大小限制"),
        415: ("unsupported_format", "供应商不支持该文件格式"),
    }.get(status, ("service_error", f"文档解析服务返回 HTTP {status}"))
    return DocumentParsingError(code, message)


def _log(context: dict | None, event: str, **fields) -> None:
    emit(event, **{**(context or {}), **fields})


@asynccontextmanager
async def _timed_stage(provider: str, phase: str, context: dict | None, task_id: str | None = None):
    started = time.monotonic()
    status = "completed"
    try:
        yield
    except asyncio.CancelledError:
        status = "cancelled"
        raise
    except Exception:
        status = "failed"
        raise
    finally:
        _log(context, "document_parse_phase", provider_name=provider, model_name=PROVIDERS[provider].model,
             phase=phase, status=status, document_task_id=task_id,
             elapsed_ms=int((time.monotonic() - started) * 1000))


@asynccontextmanager
async def _stream(client, method, url, **kwargs):
    hostname = kwargs.get("extensions", {}).get("sni_hostname")
    async with proxy_compatible_web_fetch(server_hostname=hostname):
        async with client.stream(method, url, follow_redirects=False, **kwargs) as response:
            yield response


async def _request(client, method: str, url: str, *, provider: str, context: dict | None,
                   signed: bool = False, max_bytes: int = _MAX_JSON_BYTES, **kwargs) -> bytes:
    """Bound reads, pin signed URLs to validated public IPs, retry only GET."""
    current = url
    redirects = 0
    for attempt in range(4):
        request_url = current
        if signed:
            install_proxy_compatibility()
            parsed = urlsplit(current)
            if parsed.scheme != "https" or parsed.username or parsed.password:
                raise DocumentParsingError("unsafe_url", "供应商返回了不安全的上传或下载地址")
            try:
                resolved = await validate_and_resolve_url(current, allow_local=False)
            except (ValueError, OSError) as exc:
                raise DocumentParsingError("unsafe_url", "供应商下载或上传地址未通过公网安全校验") from exc
            request_url = build_url_with_ip(resolved)
            kwargs["headers"] = {"Host": resolved.hostname, "Accept-Encoding": "identity"}
            kwargs["extensions"] = {"sni_hostname": resolved.hostname}
        path = "/signed-artifact" if signed else urlsplit(url).path
        path = re.sub(r"/[A-Za-z0-9_.-]{8,}$", "/{task}", path) if method == "GET" else path
        endpoint = urlsplit(current)
        origin = f"{endpoint.scheme}://{endpoint.netloc}"
        _log(context, "document_http_request", provider_name=provider, http_method=method, document_api_path=path,
             target_origin=origin, attempt=attempt + 1)
        started = time.monotonic()
        try:
            async with _stream(client, method, request_url, **kwargs) as response:
                if not 200 <= response.status_code < 300:
                    _log(context, "document_http_response", provider_name=provider, http_method=method,
                         document_api_path=path, target_origin=origin, http_status=response.status_code,
                         elapsed_ms=int((time.monotonic() - started) * 1000))
                if signed and method == "GET" and response.status_code in (301, 302, 303, 307, 308):
                    redirects += 1
                    if redirects > 3 or "location" not in response.headers:
                        raise DocumentParsingError("unsafe_url", "供应商下载重定向次数过多或无效")
                    current = urljoin(current, response.headers["location"])
                    continue
                if response.status_code >= 400:
                    if method == "GET" and attempt < 3 and (response.status_code == 429 or response.status_code >= 500):
                        retry = response.headers.get("Retry-After", "")
                        delay = min(float(retry), 30.0) if retry.isdigit() else min(2 ** attempt, 10)
                        await asyncio.sleep(delay)
                        continue
                    raise _status_error(response.status_code, signed=signed)
                if not 200 <= response.status_code < 300:
                    raise DocumentParsingError("service_error", "供应商返回了意外重定向或HTTP状态")
                buffer = bytearray()
                async for chunk in response.aiter_bytes():
                    if len(buffer) + len(chunk) > max_bytes:
                        raise DocumentParsingError("result_too_large", "供应商解析产物超过安全下载限制")
                    buffer.extend(chunk)
                _log(context, "document_http_response", provider_name=provider, http_method=method,
                     document_api_path=path, target_origin=origin, http_status=response.status_code,
                     elapsed_ms=int((time.monotonic() - started) * 1000))
                return bytes(buffer)
        except httpx2.RequestError as exc:
            if method == "GET" and attempt < 3:
                await asyncio.sleep(min(2 ** attempt, 10))
                continue
            code = "submission_unknown" if method in ("POST", "PUT") else "network_error"
            raise DocumentParsingError(code, "供应商请求连接异常，未自动重新提交解析任务") from exc
    raise DocumentParsingError("network_error", "供应商查询或下载重试次数已耗尽")


def _body(blob: bytes) -> dict:
    try:
        body = json.loads(blob)
        if not isinstance(body, dict):
            raise ValueError()
        return body
    except (ValueError, UnicodeError) as exc:
        raise DocumentParsingError("invalid_result", "供应商返回的JSON结构无效") from exc


def _data(body: dict) -> dict:
    code = body.get("code", body.get("errorCode", 0))
    if code not in (0, "0", None):
        # MinerU's documented codes; never infer auth from generic parsing errors.
        categories = {
            "A0202": ("invalid_key", "API Key 无效"), "A0211": ("invalid_key", "API Key 已过期"),
            "-60001": ("service_error", "供应商无法生成上传地址"),
            "-60002": ("unsupported_format", "供应商未识别到支持的文件格式"),
            "-60003": ("invalid_file", "供应商无法读取文件，请检查文件是否损坏"),
            "-60004": ("invalid_file", "供应商检测到空文件"),
            "-60005": ("file_too_large", "文件超过供应商大小限制"),
            "-60006": ("too_many_pages", "文件超过供应商页数限制"),
            "-10001": ("service_error", "文档解析供应商服务异常"),
            "-10002": ("invalid_request", "供应商拒绝请求参数"),
        }
        if str(code) in categories:
            raise DocumentParsingError(*categories[str(code)])
        safe_code = str(code) if type(code) in (int, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,24}", str(code)) else "unknown"
        raise DocumentParsingError("provider_rejected", f"供应商拒绝解析请求（错误代码 {safe_code}）")
    data = body.get("data", body)
    if not isinstance(data, dict):
        raise DocumentParsingError("invalid_result", "供应商返回的任务数据无效")
    return data


def _task_id(value: Any) -> str:
    if type(value) is not str or not _IDENTIFIER.fullmatch(value):
        raise DocumentParsingError("invalid_result", "供应商返回的任务标识无效")
    return value


def _unpack(blob: bytes, dest: Path) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(blob)) as archive:
            entries = archive.infolist()
            if len(entries) > 20_000 or sum(e.file_size for e in entries) > _MAX_RESULT_BYTES:
                raise DocumentParsingError("result_too_large", "解析压缩包超过安全解包限制")
            root = dest.resolve()
            for entry in entries:
                relative = PurePosixPath(entry.filename)
                mode = entry.external_attr >> 16
                if (relative.is_absolute() or ".." in relative.parts or "\\" in entry.filename or stat.S_ISLNK(mode)
                        or not (root / entry.filename).resolve().is_relative_to(root)):
                    raise DocumentParsingError("unsafe_archive", "解析压缩包包含不安全路径")
            for entry in entries:
                target = root / entry.filename
                if entry.is_dir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                else:
                    _save(target, archive.read(entry))
    except (zipfile.BadZipFile, OSError, RuntimeError) as exc:
        raise DocumentParsingError("invalid_result", "供应商解析压缩包损坏") from exc


def _block_text(item: dict) -> str:
    # Copy native field values only. No title rewriting or OCR correction.
    output: list[str] = []
    for key in ("table_caption", "image_caption", "chart_caption", "list_items"):
        value = item.get(key)
        if isinstance(value, list):
            output.extend(str(x) for x in value)
    for key in ("text", "table_body", "html", "latex", "image_body", "chart_body"):
        value = item.get(key)
        if isinstance(value, str):
            output.append(value)
    for key in ("table_footnote", "image_footnote", "chart_footnote"):
        value = item.get(key)
        if isinstance(value, list):
            output.extend(str(x) for x in value)
    if item.get("img_path"):
        output.append(f"![]({item['img_path']})")
    if not output:
        output.append(json.dumps(item, ensure_ascii=False))
    return "\n\n".join(output)


def _mineru_pages(dest: Path) -> list[DocumentPage]:
    candidates = sorted(dest.rglob("*_content_list.json"))
    if not candidates:
        raise DocumentParsingError("invalid_result", "MinerU产物缺少分页内容索引")
    blocks = json.loads(candidates[0].read_bytes())
    if not isinstance(blocks, list):
        raise DocumentParsingError("invalid_result", "MinerU分页内容索引无效")
    by_page: dict[int | None, list[dict]] = {}
    for block in blocks:
        if not isinstance(block, dict):
            raise DocumentParsingError("invalid_result", "MinerU内容块无效")
        idx = block.get("page_idx")
        page = idx + 1 if type(idx) is int and idx >= 0 else None
        by_page.setdefault(page, []).append(block)
    # v2 outer sequence supplies blank physical pages absent in flat content_list.
    v2 = sorted(dest.rglob("*_content_list_v2.json"))
    if v2:
        outer = json.loads(v2[0].read_bytes())
        if isinstance(outer, list) and all(isinstance(p, list) for p in outer):
            for number in range(1, len(outer) + 1):
                by_page.setdefault(number, [])
    return [DocumentPage(number, "\n\n".join(_block_text(b) for b in items), native={"blocks": items})
            for number, items in sorted(by_page.items(), key=lambda item: (item[0] is None, item[0] or 0))]


def _paddle_pages(blob: bytes) -> list[DocumentPage]:
    pages: list[DocumentPage] = []
    for line in blob.decode("utf-8").splitlines():
        if not line.strip():
            continue
        body = _data(_body(line.encode()))
        result = body.get("result", body)
        for item in result["layoutParsingResults"]:
            # AI Studio JSONL layoutParsingResults are ordered input pages.
            index = item.get("prunedResult", {}).get("page_index")
            number = index + 1 if type(index) is int and index >= 0 else len(pages) + 1
            text = item.get("markdown", {}).get("text")
            if not isinstance(text, str):
                raise DocumentParsingError("invalid_result", "PaddleOCR页面缺少原生Markdown")
            pages.append(DocumentPage(number, text, native=item))
    return pages


def _glm_pages(body: dict) -> list[DocumentPage]:
    markdown = body.get("md_results")
    if isinstance(markdown, list) and all(isinstance(p, str) for p in markdown):
        return [DocumentPage(i + 1, value) for i, value in enumerate(markdown)]
    layout = body.get("layout_details")
    if isinstance(layout, list) and all(isinstance(p, list) for p in layout) and layout:
        pages = []
        for number, blocks in enumerate(layout, 1):
            if not all(isinstance(b, dict) and (b.get("content") is None or isinstance(b.get("content"), str)) for b in blocks):
                raise DocumentParsingError("invalid_result", "GLM-OCR页面布局内容无效")
            pages.append(DocumentPage(number, "\n\n".join(b["content"] for b in blocks if isinstance(b.get("content"), str)), native={"blocks": blocks}))
        return pages
    if isinstance(markdown, str):
        # No page boundaries supplied: retain text, never label it physical page 1.
        return [DocumentPage(None, markdown)]
    raise DocumentParsingError("invalid_result", "GLM-OCR未返回可读正文")


def _result(provider: str, source: DocumentSource, pages: list[DocumentPage], native: dict) -> ParsedDocument:
    if not pages:
        raise DocumentParsingError("invalid_result", "供应商未返回任何页面或正文")
    known = {p.page for p in pages if p.page is not None}
    expected = source.source_pages
    warnings: list[str] = []
    status = "complete"
    unknown_boundaries = any(p.page is None for p in pages)
    if expected is not None and unknown_boundaries:
        status = "partial"
        warnings.append("原文件页数已知，但供应商页边界不完整，无法确认物理页覆盖；不能据此判定具体缺失页")
    elif expected is not None:
        missing = sorted(set(range(1, expected + 1)) - known)
        if missing:
            status = "partial"
            warnings.append("供应商未提供完整原始页覆盖，缺失页：" + ",".join(map(str, missing)))
    if unknown_boundaries:
        warnings.append("供应商未提供可靠物理页边界，该内容以未知页码返回")
    return ParsedDocument(provider, PROVIDERS[provider].model, pages, expected, status, warnings, native)


async def _parse(config, source, cache_dir, client, context):
    provider = config.provider
    signature = hashlib.sha256(source.data + provider.encode() + config.model.encode()).hexdigest()
    task_path = cache_dir / "task.json"
    task = await _cpu(_load_task, task_path, signature)
    task["provider"] = provider
    auth = {"Authorization": f"Bearer {config.api_key}"}

    async def request(method, url, **kwargs):
        kwargs.setdefault("headers", auth)
        return await _request(client, method, url, provider=provider, context=context, **kwargs)

    async def submit(url, **kwargs):
        async with _timed_stage(provider, "submit", context):
            task["submission_unknown"] = True
            await _cpu(_save, task_path, task)
            blob = await request("POST", url, **kwargs)
            await _cpu(_save, cache_dir / "submission.json", blob)
            return _data(_body(blob))

    if provider == "glm":
        output = cache_dir / "response.json"
        if not output.exists():
            if task.get("submitted"):
                raise DocumentParsingError("submission_unknown", "GLM-OCR响应未完整保存，未重新提交解析任务")
            data = await submit(_GLM, json={"model": "glm-ocr", "file": f"data:{source.mime_type};base64," + base64.b64encode(source.data).decode("ascii")})
            await _cpu(_save, output, data)
            task.update(submission_unknown=False, submitted=True)
            await _cpu(_save, task_path, task)
        async with _timed_stage(provider, "parse", context):
            body = await _cpu(_body, await _cpu(output.read_bytes))
            return _result(provider, source, await _cpu(_glm_pages, body), body)

    if not task.get("task_id"):
        if provider == "mineru":
            data = await submit(_MINERU + "/file-urls/batch", json={"files": [{"name": source.name, "data_id": signature[:24]}],
                              "model_version": "vlm", "enable_table": True, "enable_formula": True, "language": "ch"})
            task.update(task_id=_task_id(data["batch_id"]), upload_url=data["file_urls"][0], submission_unknown=False)
        else:
            data = await submit(_PADDLE, data={"model": "PaddleOCR-VL-1.6", "optionalPayload": json.dumps({"mergeTables": True, "relevelTitles": True})},
                                files={"file": (source.name, source.data, source.mime_type)})
            task.update(task_id=_task_id(data["jobId"]), submission_unknown=False, uploaded=True)
        await _cpu(_save, task_path, task)
    task_id = _task_id(task["task_id"])
    _log(context, "document_parse_phase", provider_name=provider, model_name=PROVIDERS[provider].model, phase="submitted", document_task_id=task_id)
    if provider == "mineru" and task.get("upload_unknown"):
        # Recover the original task rather than creating a second paid task.
        blob = await request("GET", _MINERU + f"/extract-results/batch/{task_id}")
        await _cpu(_save, cache_dir / "status.json", blob)
        state = _data(_body(blob))["extract_result"][0]["state"]
        if state in {"pending", "running", "done"}:
            task.update(uploaded=True, upload_unknown=False)
            task.pop("upload_url", None)
        elif state == "waiting-file":
            # The original signed-object PUT is idempotent and remote state
            # confirms that no complete file was received. Keep the same URL/ID.
            task["upload_unknown"] = False
        elif state == "failed":
            raise DocumentParsingError("remote_task_failed", "供应商原任务已失败，未重新提交")
        else:
            raise DocumentParsingError("submission_unknown", "原任务上传状态仍不明确，未自动重传")
        await _cpu(_save, task_path, task)
    if provider == "mineru" and not task.get("uploaded"):
        async with _timed_stage(provider, "upload", context, task_id):
            task["upload_unknown"] = True
            await _cpu(_save, task_path, task)
            await request("PUT", task["upload_url"], signed=True, content=source.data)
            task.update(uploaded=True, upload_unknown=False)
            task.pop("upload_url", None)
            await _cpu(_save, task_path, task)
    output = cache_dir / ("result.zip" if provider == "mineru" else "result.jsonl")
    while not output.exists():
        async with _timed_stage(provider, "wait", context, task_id):
            url = _MINERU + f"/extract-results/batch/{task_id}" if provider == "mineru" else _PADDLE + f"/{task_id}"
            blob = await request("GET", url)
            await _cpu(_save, cache_dir / "status.json", blob)
            data = _data(_body(blob))
            state = data["extract_result"][0] if provider == "mineru" else data
            _log(context, "document_parse_phase", provider_name=provider, model_name=PROVIDERS[provider].model,
                 phase="poll", status=state.get("state", "unknown"), document_task_id=task_id)
            if state["state"] == "failed":
                raise DocumentParsingError("remote_task_failed", "供应商解析任务失败；原任务已保留，未重新提交")
            if state["state"] != "done":
                await asyncio.sleep(POLL_SECONDS)
        if state["state"] == "done":
            async with _timed_stage(provider, "download", context, task_id):
                url = state["full_zip_url"] if provider == "mineru" else state["resultUrl"]["jsonUrl"]
                raw = await request("GET", url, signed=True, max_bytes=_MAX_RESULT_BYTES)
                await _cpu(_save, output, raw)
            break
    async with _timed_stage(provider, "parse", context, task_id):
        raw = await _cpu(output.read_bytes)
        if provider == "mineru":
            dest = cache_dir / "artifacts"
            await _cpu(_unpack, raw, dest)
            pages = await _cpu(_mineru_pages, dest)
            native = {"task_id": task_id, "artifact_directory": "artifacts"}
        else:
            pages = await _cpu(_paddle_pages, raw)
            native = {"task_id": task_id, "artifact_file": "result.jsonl"}
        return _result(provider, source, pages, native)


async def parse_document(config: DocumentParsingRuntimeConfig, source: DocumentSource, cache_dir: Path, *,
                         client: httpx2.AsyncClient | None = None, context: dict | None = None) -> ParsedDocument:
    if config.mode != "provider" or config.provider not in PROVIDERS or not config.api_key or config.configuration_error:
        raise DocumentParsingError("unconfigured", "请管理员先配置有效的文档解析供应商和 API Key")
    capability = PROVIDERS[config.provider]
    if not capability.supports(source.name, source.mime_type):
        raise DocumentParsingError("unsupported_format", "当前文档解析供应商不支持此文件格式")
    if len(source.data) > capability.max_bytes:
        raise DocumentParsingError("file_too_large", "文件超过供应商大小限制")
    if capability.max_pages and source.source_pages and source.source_pages > capability.max_pages:
        raise DocumentParsingError("too_many_pages", "文件超过供应商页数限制")
    cache_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    cache_dir.chmod(0o700)
    started = time.monotonic()
    _log(context, "document_parse_started", provider_name=config.provider, model_name=config.model,
         byte_size=len(source.data), source_hash=hashlib.sha256(source.data).hexdigest(),
         file_type=Path(source.name).suffix.lower()[1:], phase="start")
    parameters = ({"parse_language": "ch", "parse_table_enabled": True, "parse_formula_enabled": True}
                  if config.provider == "mineru" else {"parse_merge_tables": True, "parse_relevel_titles": True}
                  if config.provider == "paddle" else {})
    _log(context, "document_parse_phase", provider_name=config.provider, model_name=config.model, phase="parameters", **parameters)
    try:
        async with asyncio.timeout(PARSE_TIMEOUT_SECONDS):
            if client is None:
                async with httpx2.AsyncClient(timeout=httpx2.Timeout(900, connect=30), follow_redirects=False) as owned:
                    result = await _parse(config, source, cache_dir, owned, context)
            else:
                result = await _parse(config, source, cache_dir, client, context)
        _log(context, "document_parse_completed", provider_name=config.provider, model_name=config.model,
             returned_pages=len(result.pages), status=result.status, elapsed_ms=int((time.monotonic() - started) * 1000))
        return result
    except asyncio.CancelledError:
        _log(context, "document_parse_cancelled", provider_name=config.provider, phase="cancel")
        raise
    except TimeoutError as exc:
        _log(context, "document_parse_failed", provider_name=config.provider, error_code="timeout")
        raise DocumentParsingError("timeout", "解析等待超过900秒；远端任务可能仍在运行，未重新提交") from exc
    except DocumentParsingError as exc:
        _log(context, "document_parse_failed", provider_name=config.provider, error_code=exc.code)
        raise
    except (KeyError, ValueError, TypeError, UnicodeError, OSError) as exc:
        _log(context, "document_parse_failed", provider_name=config.provider, error_code="invalid_result")
        raise DocumentParsingError("invalid_result", "供应商解析产物结构无效或无法读取") from exc
