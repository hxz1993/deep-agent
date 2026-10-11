"""Explicit, run-scoped evidence export; never an automatic result spill."""
from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
from collections.abc import Awaitable, Callable, Mapping
from typing import Literal

from pydantic import BaseModel, ConfigDict

from deep_agent.agent.media_pipeline import max_upload_bytes
from deep_agent.files.storage import (
    FileStore,
    FileStorePermissionError,
    LocalFileStore,
    PlatformFileStore,
    StoredFile,
)
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT
from deepfos_platform.errors.classes import (
    APINotAvailError,
    APIStatusNotTrue,
    ServerInstanceNotFound,
    ServerNotFound,
)
from .models import DocumentParsingError


class EvidenceFileReference(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    storage: Literal["local", "platform"]
    file_id: str
    file_name: str
    byte_size: int
    mime: Literal["application/zip"] = "application/zip"
    sha256: str
    schema_version: str


class ExportError(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str


class ExportResult(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    status: Literal["not_requested", "saved", "failed", "unknown"] = "not_requested"
    file: EvidenceFileReference | None = None
    error: ExportError | None = None


EvidencePayload = bytes | Callable[[], bytes | Awaitable[bytes]]


def _scope(file_store: FileStore | None, request_headers: Mapping[str, str]) -> str:
    headers = {str(key).lower(): str(value) for key, value in request_headers.items()}
    return hashlib.sha256(repr((id(file_store), sorted(headers.items()))).encode()).hexdigest()


def _failure(code: str, message: str, *, unknown: bool = False) -> ExportResult:
    # Messages are fixed operational facts. Upstream exceptions can contain
    # credentials, signed URLs and response bodies, so do not interpolate them.
    return ExportResult(
        status="unknown" if unknown else "failed",
        error=ExportError(code=code, message=message),
    )


def _upload_failure(exc: Exception, *, storage: str) -> ExportResult:
    if isinstance(exc, (FileStorePermissionError, PermissionError)):
        return _failure("permission_denied", "当前身份无权上传证据文件；解析正文仍可读取。")
    if isinstance(exc, (APINotAvailError, ServerNotFound, ServerInstanceNotFound)):
        return _failure("store_unavailable", "文件服务不可用，未能提交上传；解析正文仍可读取。")
    status = getattr(exc, "upstream_http_status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if isinstance(exc, APIStatusNotTrue) or (type(status) is int and 400 <= status < 500 and status != 408):
        return _failure("upload_rejected", "文件服务已明确拒绝上传；本次Run不会自动重传，解析正文仍可读取。")
    if storage == "local":
        # LocalFileStore rolls back both its Blob and metadata on write errors.
        return _failure("local_write_failed", "本地证据文件写入失败；解析正文仍可读取。")
    return _failure(
        "upload_outcome_unknown",
        "上传已发起，但无法确认文件是否保存；本次Run不会自动重传，解析正文仍可读取。",
        unknown=True,
    )


def _reference(
    uploaded: StoredFile, *, storage: Literal["local", "platform"], data: bytes,
    schema_version: str,
) -> EvidenceFileReference | None:
    file_id = uploaded.id
    name = uploaded.file_name
    if not isinstance(file_id, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,256}", file_id):
        return None
    if file_id.startswith("local_") != (storage == "local"):
        return None
    if (not isinstance(name, str) or not name or len(name) > 255
            or "/" in name or "\\" in name or any(ord(char) < 32 for char in name)):
        return None
    if type(uploaded.file_size) is not int or uploaded.file_size != len(data):
        return None
    return EvidenceFileReference(
        storage=storage, file_id=file_id, file_name=name, byte_size=len(data),
        sha256=hashlib.sha256(data).hexdigest(), schema_version=schema_version,
    )


class DocumentExportManager:
    """One export attempt per document, with shared waiters and sticky outcomes.

    A cancelled tool waiter does not cancel another waiter's upload. Run cleanup
    cancels remote waiting (which cannot prove remote rollback), while local
    writes finish and are joined because cancelling to_thread cannot stop a write.
    Successful files belong to FileStore and are never deleted by Run cleanup.
    """

    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task[ExportResult]] = {}
        self._results: dict[str, ExportResult] = {}
        self._scopes: dict[str, str] = {}
        self._closed = False
        self._close_task: asyncio.Task[None] | None = None

    def peek(self, document_id: str) -> ExportResult:
        """Internal status lookup; use peek_scoped before returning file authority."""
        return self._results.get(document_id, ExportResult())

    def peek_scoped(
        self, document_id: str, *, file_store: FileStore | None,
        request_headers: Mapping[str, str],
    ) -> ExportResult:
        """Read an existing outcome without starting an upload or changing scope."""
        if document_id in self._scopes and self._scopes[document_id] != _scope(file_store, request_headers):
            return _failure("identity_changed", "当前文件存储或身份与首次导出不同，不能复用原证据文件引用。")
        return self.peek(document_id)

    async def ensure(
        self,
        document_id: str,
        payload: EvidencePayload,
        *,
        file_store: FileStore | None,
        request_headers: Mapping[str, str],
        schema_version: str = "document-evidence-v2",
    ) -> ExportResult:
        if self._closed:
            raise RuntimeError("Document export cache is closed")
        # Cache identity is a digest only. Authorization is rechecked by the
        # tool for each read; this guard additionally forbids reusing an export
        # under a changed store or trusted request identity inside one Run.
        headers = {str(key).lower(): str(value) for key, value in request_headers.items()}
        scope = _scope(file_store, headers)
        if document_id in self._scopes and self._scopes[document_id] != scope:
            return _failure("identity_changed", "当前文件存储或身份与首次导出不同，不能复用原证据文件引用。")
        self._scopes.setdefault(document_id, scope)
        if document_id in self._results:
            return self._results[document_id]
        task = self._tasks.get(document_id)
        if task is None:
            task = asyncio.create_task(self._export(
                document_id, payload, file_store=file_store,
                request_headers=headers, schema_version=schema_version,
            ))
            self._tasks[document_id] = task
        return await asyncio.shield(task)

    async def _export(
        self, document_id: str, payload: EvidencePayload, *, file_store: FileStore | None,
        request_headers: Mapping[str, str], schema_version: str,
    ) -> ExportResult:
        def finish(result: ExportResult) -> ExportResult:
            self._results[document_id] = result
            return result

        storage: Literal["local", "platform"]
        if isinstance(file_store, LocalFileStore):
            storage = "local"
        elif isinstance(file_store, PlatformFileStore):
            storage = "platform"
        else:
            return finish(_failure("store_unavailable", "当前没有可用的文件存储，解析正文仍可读取。"))
        if not request_headers.get("user", "").strip():
            return finish(_failure("identity_missing", "当前请求缺少可信用户身份，不能导出证据文件。"))
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,96}", document_id):
            return finish(_failure("invalid_document", "解析结果引用无效，不能生成证据文件。"))
        if not isinstance(schema_version, str) or not schema_version or len(schema_version) > 100:
            return finish(_failure("invalid_schema", "证据文件Schema版本无效。"))
        try:
            data = payload() if callable(payload) else payload
            if inspect.isawaitable(data):
                data = await data
            if not isinstance(data, bytes):
                return finish(_failure("serialization_failed", "证据序列化未生成文件字节，解析正文仍可读取。"))
        except asyncio.CancelledError:
            raise
        except DocumentParsingError as exc:
            if exc.code == "bundle_too_large":
                return finish(_failure("file_too_large", "证据包超过文件上传或解包安全上限；解析正文仍可读取。"))
            if exc.code == "bundle_page_limit":
                return finish(_failure("bundle_page_limit", "证据包超过1000页安全条目上限；解析正文仍可读取。"))
            return finish(_failure("serialization_failed", "证据包生成失败，解析正文仍可读取。"))
        except Exception:
            return finish(_failure("serialization_failed", "证据文件序列化失败，解析正文仍可读取。"))
        if len(data) > max_upload_bytes():
            return finish(_failure("file_too_large", "证据文件超过当前文件上传大小上限，解析正文仍可读取。"))
        upload = file_store.upload_once if isinstance(file_store, PlatformFileStore) else file_store.upload
        upload_task = asyncio.create_task(upload(
            file_type=FILE_TYPE_ATTACHMENT, file_name=f"{document_id}-evidence.zip",
            mime="application/zip", data=data, request_headers=request_headers,
        ))
        try:
            uploaded = await asyncio.shield(upload_task)
        except asyncio.CancelledError:
            if storage == "local":
                # Do not orphan a thread that can still commit a local file.
                try:
                    uploaded = await upload_task
                except Exception as exc:
                    return finish(_upload_failure(exc, storage=storage))
            else:
                upload_task.cancel()
                await asyncio.gather(upload_task, return_exceptions=True)
                finish(_failure("upload_cancelled", "上传等待已取消，远端是否保存未知；本次Run不会自动重传。", unknown=True))
                raise
        except Exception as exc:
            return finish(_upload_failure(exc, storage=storage))
        try:
            reference = _reference(uploaded, storage=storage, data=data, schema_version=schema_version)
        except Exception:
            reference = None
        if reference is None:
            return finish(_failure("invalid_receipt", "上传响应的文件引用或大小无法确认，不能安全提供下载引用；本次Run不会自动重传。", unknown=True))
        return finish(ExportResult(status="saved", file=reference))

    def reset_failed(self, document_id: str) -> bool:
        """Explicit host recovery after correcting a known failure, never automatic.

        The model-facing tool does not invoke this. An unknown outcome cannot be
        reset because another upload could silently create a duplicate file.
        """
        if self._closed or self.peek(document_id).status != "failed":
            return False
        task = self._tasks.get(document_id)
        if task is not None and not task.done():
            return False
        self._results.pop(document_id, None)
        self._tasks.pop(document_id, None)
        self._scopes.pop(document_id, None)
        return True

    async def aclose(self) -> None:
        self._closed = True
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close())
        await asyncio.shield(self._close_task)

    async def _close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._results.clear()
        self._scopes.clear()
