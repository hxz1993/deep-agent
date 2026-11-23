"""Bounded, ephemeral trajectory archives. File authority comes only from events."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import tempfile
import threading
import time
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from urllib.parse import quote

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError
from starlette.responses import Response, StreamingResponse

from deep_agent.agent.attachments import index_attachment_refs
from deep_agent.agent.headers import extract_execution_context_headers
from deep_agent.agent.tool_output_limits import session_file_metadata_from_history
from deep_agent.db.event_codec import decode_message_events
from deep_agent.db.models import ChatSession
from deep_agent.db.session_scope import agent_scope_to_api, resolve_session
from deep_agent.files.storage import LocalFileStore, FileStoreError, FileStoreSizeLimitError
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT
from sqlalchemy import select

MAX_FILES = 100
MAX_TRACE_BYTES = 64 * 1024 * 1024
MAX_TOTAL_BYTES = 200 * 1024 * 1024
TIMEOUT_SECONDS = 120
_GATE = threading.Lock()
_CHUNK = 64 * 1024


class ExportBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    trajectory: dict
    event_head_sequence: StrictInt = Field(ge=0)
    snapshot_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


EXPORT_OPENAPI = {
    "requestBody": {"required": True, "content": {"application/json": {
        "schema": ExportBody.model_json_schema()}}},
    "responses": {"200": {"description": "JSON trajectory or ZIP archive, selected by include_files",
        "content": {"application/json": {"schema": {"type": "object"}},
                    "application/zip": {"schema": {"type": "string", "format": "binary"}}},
        "headers": {
            "X-Deep-Agent-Export-Status": {"schema": {"type": "string", "enum": ["complete", "partial"]}},
            "X-Deep-Agent-Export-Included-Files": {"schema": {"type": "integer"}},
            "X-Deep-Agent-Export-Skipped-Files": {"schema": {"type": "integer"}},
        }}}}


@dataclass(frozen=True)
class ExportFile:
    file_id: str
    name: str
    category: str
    mime: str | None = None
    size: int | None = None
    unavailable: bool = False


@dataclass(frozen=True)
class ExportSnapshot:
    session_id: str
    agent_id: str | None
    owner: str | None
    head: int
    digest: str
    files: tuple[ExportFile, ...]


def _size(value):
    return value if type(value) is int and value >= 0 else None


def _snapshot(events):
    digest = hashlib.sha256()
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    for event in events:
        for chunk in encoder.iterencode(event.public_dict()):
            digest.update(chunk.encode())
        digest.update(b"\n")
    raw = decode_message_events(events)
    indexed = {ref.id: ExportFile(ref.id, ref.name or ref.id, "upload", ref.mime)
               for ref in index_attachment_refs(messages=raw).values()}
    for file_id, item in session_file_metadata_from_history(raw).items():
        if file_id not in indexed:
            indexed[file_id] = ExportFile(file_id, item.get("file_name") or file_id, "artifact",
                item.get("mime"), _size(item.get("byte_size")))
    return max((event.sequence for event in events), default=0), digest.hexdigest(), tuple(indexed.values())


def _persisted_running(events):
    execution = [event for event in events if event.event_type in {
        "user/message", "assistant/message", "tool/results", "model/request", "execution/error"}]
    if not execution:
        return False
    run_id = execution[-1].run_id
    current = [event for event in execution if event.run_id == run_id]
    if any(event.event_type == "execution/error" for event in current):
        return False
    assistants = [event for event in current if event.event_type == "assistant/message"]
    if assistants:
        return bool(run_id) and assistants[-1].status not in {
            "pending", "completed", "cancelled", "failed", "interrupted", "timed_out", "stale"}
    # A new persisted input without a terminal assistant may be executing on a
    # different worker. Legacy unscoped records retain their old readability.
    return bool(run_id)


async def load_snapshot(ctx, session_id, user_id, *, agent_id=None, admin=False, require_idle=True):
    if admin:
        async with ctx.db_session_factory() as db:
            row = (await db.execute(select(ChatSession).where(ChatSession.session_id == session_id))).scalar_one_or_none()
            if row is None:
                raise HTTPException(404, "会话不存在或已删除")
            agent_id, owner = agent_scope_to_api(row.agent_id), row.user_id
    else:
        resolved = await resolve_session(ctx.store, session_id, agent_id=agent_id, user_id=user_id)
        if resolved is None:
            raise HTTPException(404, "会话不存在或无权访问")
        agent_id, session_id = agent_scope_to_api(resolved[0]), resolved[1]
        owner = user_id
    if require_idle and ctx.active_runs.get(agent_id=agent_id, session_id=session_id) is not None:
        raise HTTPException(409, "会话仍在运行，请结束或停止后再导出文件")
    events = await ctx.store.list_all_events(session_id)
    if require_idle and _persisted_running(events):
        raise HTTPException(409, "会话仍在运行，请结束或停止后再导出文件")
    try:
        head, digest, files = await _thread(_snapshot, events, stop=threading.Event())
    except (ValueError, TypeError, RecursionError):
        raise HTTPException(409, "会话文件引用无法完整读取，请先核对历史轨迹") from None
    return ExportSnapshot(session_id, agent_id, owner, head, digest, files)


def _file_headers(request, user_id, snapshot, file_store, admin):
    headers = extract_execution_context_headers(request)
    # The existing administrator local-file proxy authorizes by session owner.
    # Platform calls MUST keep the actual caller, never the historical owner.
    headers["user"] = snapshot.owner if admin and isinstance(file_store, LocalFileStore) else user_id
    return headers


async def preview(ctx, request, session_id, user_id, *, agent_id=None, admin=False):
    snapshot = await load_snapshot(ctx, session_id, user_id, agent_id=agent_id, admin=admin)
    snapshot = await _describe_files(ctx, request, snapshot, user_id, admin)
    files = snapshot.files
    total = sum(item.size or 0 for item in files)
    allowed = len(files) <= MAX_FILES and total <= MAX_TOTAL_BYTES
    return {"session_id": snapshot.session_id, "event_head_sequence": snapshot.head,
        "snapshot_digest": snapshot.digest, "file_count": len(files),
        "upload_count": sum(item.category == "upload" for item in files),
        "artifact_count": sum(item.category == "artifact" for item in files),
        "known_size_bytes": total, "unknown_size_count": sum(item.size is None for item in files),
        "can_export": allowed, "message": None if allowed else "文件数量或总大小超过导出上限，请仅导出轨迹或分别下载文件",
        "limits": {"max_files": MAX_FILES, "max_trace_bytes": MAX_TRACE_BYTES,
                   "max_total_bytes": MAX_TOTAL_BYTES, "timeout_seconds": TIMEOUT_SECONDS}}


async def _describe_files(ctx, request, snapshot, user_id, admin):
    files = []
    if isinstance(ctx.file_store, LocalFileStore) and len(snapshot.files) <= MAX_FILES:
        headers = _file_headers(request, user_id, snapshot, ctx.file_store, admin)
        for item in snapshot.files:
            try:
                info = await ctx.file_store.describe(item.file_id, headers)
                if info.file_type != FILE_TYPE_ATTACHMENT:
                    raise FileStoreError("not an attachment")
                item = replace(item, name=info.file_name, mime=info.mime, size=info.file_size)
            except FileStoreError:
                item = replace(item, unavailable=True)
            files.append(item)
    else:
        files = list(snapshot.files)
    return replace(snapshot, files=tuple(files))


async def _body(request):
    payload = bytearray()
    async for chunk in request.stream():
        if len(payload) + len(chunk) > MAX_TRACE_BYTES + 1024 * 1024:
            raise HTTPException(413, "轨迹请求超过大小上限")
        payload.extend(chunk)
    def parse():
        try:
            value = ExportBody.model_validate(json.loads(payload))
            trace = value.trajectory
            if trace.get("format") != "deepagent-session-trajectory" or trace.get("version") != 1 or not isinstance(trace.get("events"), list):
                raise ValueError("invalid trajectory")
            data = json.dumps(trace, ensure_ascii=False, indent=2, allow_nan=False).encode()
            if len(data) > MAX_TRACE_BYTES:
                raise HTTPException(413, "轨迹 JSON 超过 64 MiB 上限")
            return value, data
        except (ValidationError, ValueError, TypeError, RecursionError, UnicodeError):
            raise HTTPException(422, "轨迹导出参数无效") from None
    return await _thread(parse, stop=threading.Event())


async def _thread(function, *args, stop):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        stop.set()
        while not task.done():
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                continue
            except BaseException:
                break
        # Observe the worker exception before deleting anything it could use.
        if task.done() and not task.cancelled():
            task.exception()
        raise


def _write_entry(archive, name, data, stop, deadline=None):
    digest = hashlib.sha256()
    with archive.open(name, "w") as target:
        for offset in range(0, len(data), _CHUNK):
            if stop.is_set():
                raise InterruptedError("export cancelled")
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("export budget exhausted")
            chunk = data[offset:offset + _CHUNK]
            target.write(chunk)
            digest.update(chunk)
    return digest.hexdigest()


def _filename(index, value):
    value = re.sub(r'[\\/\x00-\x1f\x7f:*?"<>|]', "_", str(value))[:160].strip(" .") or "file"
    return f"files/{index:03d}-{value}"


class ArchiveResponse(StreamingResponse):
    def __init__(self, path, temporary, cleanup, headers):
        self.temporary, self.cleanup = temporary, cleanup
        async def content():
            with path.open("rb") as source:
                while chunk := await _thread(source.read, _CHUNK, stop=threading.Event()):
                    yield chunk
        super().__init__(content(), media_type="application/zip", headers=headers)

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            try:
                close = getattr(self.body_iterator, "aclose", None)
                if close is not None:
                    task = asyncio.create_task(close())
                    cancelled = False
                    while not task.done():
                        try:
                            await asyncio.shield(task)
                        except asyncio.CancelledError:
                            cancelled = True
                    task.result()
                    if cancelled:
                        raise asyncio.CancelledError
            finally:
                self.cleanup()


async def _build(ctx, request, snapshot, user_id, trace, *, admin, deadline):
    stop = threading.Event()
    temporary = tempfile.TemporaryDirectory(prefix="deepagent-trajectory-TMP_to_delete-")
    path = Path(temporary.name) / "trajectory.zip"
    archive = None
    # Reserve a small portion of the same overall budget for a valid manifest
    # and ZIP directory after a slow source download produces a partial export.
    download_deadline = deadline - min(1, TIMEOUT_SECONDS * .1)
    included = 0
    records = []
    used = len(trace)
    headers = _file_headers(request, user_id, snapshot, ctx.file_store, admin)
    try:
        archive = await _thread(lambda: zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=1), stop=stop)
        trace_hash = await _thread(_write_entry, archive, "trajectory.json", trace, stop, deadline, stop=stop)
        exhausted = None
        for index, item in enumerate(snapshot.files, 1):
            record = {"file_id": item.file_id, "name": item.name, "category": item.category,
                      "mime": item.mime, "size_bytes": item.size, "status": "skipped"}
            records.append(record)
            if exhausted:
                record["reason"] = exhausted
                continue
            remaining = download_deadline - time.monotonic()
            if remaining <= 0:
                exhausted = record["reason"] = "timeout"
                continue
            try:
                if isinstance(ctx.file_store, LocalFileStore):
                    info = await asyncio.wait_for(ctx.file_store.describe(item.file_id, headers), remaining)
                    if info.file_type != FILE_TYPE_ATTACHMENT:
                        raise FileStoreError("not an attachment")
                data = await asyncio.wait_for(ctx.file_store.download_bounded(item.file_id, headers,
                    max_bytes=max(0, MAX_TOTAL_BYTES - used)), max(.001, download_deadline - time.monotonic()))
                if len(data) > MAX_TOTAL_BYTES - used:
                    raise FileStoreSizeLimitError("download limit")
            except (asyncio.TimeoutError, TimeoutError):
                exhausted = record["reason"] = "timeout"
                continue
            except FileStoreSizeLimitError:
                exhausted = record["reason"] = "size_limit"
                continue
            except Exception:
                record["reason"] = "unavailable"
                continue
            # Archive write errors are fatal, rather than leaving an unlisted
            # truncated entry inside an otherwise apparently valid ZIP.
            entry = _filename(index, item.name)
            content_hash = await _thread(_write_entry, archive, entry, data, stop, deadline, stop=stop)
            used += len(data)
            record.update(status="included", path=entry, size_bytes=len(data), sha256=content_hash)
            included += 1
        skipped = len(records) - included
        manifest = {"format": "deepagent-trajectory-files", "version": 1,
            "session_id": snapshot.session_id, "event_head_sequence": snapshot.head,
            "snapshot_digest": snapshot.digest, "status": "partial" if skipped else "complete",
            "included_files": included, "skipped_files": skipped,
            "trajectory": {"path": "trajectory.json", "size_bytes": len(trace), "sha256": trace_hash},
            "files": records}
        manifest_data = json.dumps(manifest, ensure_ascii=False, indent=2).encode()
        await _thread(_write_entry, archive, "manifest.json", manifest_data, stop, deadline, stop=stop)
        await _thread(archive.close, stop=stop)
        archive = None
        if time.monotonic() >= deadline:
            raise TimeoutError("export budget exhausted")
        return path, temporary, included, skipped
    except BaseException:
        stop.set()
        try:
            if archive is not None:
                await _thread(archive.close, stop=stop)
        finally:
            temporary.cleanup()
        raise


async def export_response(ctx, request, session_id, user_id, *, agent_id=None, admin=False, include_files=False):
    deadline = time.monotonic() + TIMEOUT_SECONDS
    acquired = False
    if include_files:
        acquired = _GATE.acquire(blocking=False)
        if not acquired:
            raise HTTPException(429, "当前已有文件打包任务，请稍后重试", headers={"Retry-After": "2"})
    temporary = None
    released = False
    def cleanup():
        nonlocal released
        if released:
            return
        released = True
        try:
            if temporary is not None:
                temporary.cleanup()
        finally:
            if acquired:
                _GATE.release()
    try:
        async def within_budget(awaitable):
            return await asyncio.wait_for(awaitable, max(.001, deadline - time.monotonic()))

        body, trace = await within_budget(_body(request))
        if body.trajectory.get("session_id") != session_id:
            raise HTTPException(422, "轨迹与当前会话不匹配")
        snapshot = await within_budget(load_snapshot(ctx, session_id, user_id, agent_id=agent_id,
                                                     admin=admin, require_idle=include_files))
        if not include_files:
            cleanup()
            return Response(trace, media_type="application/json", headers={"Cache-Control": "no-store",
                "Content-Disposition": f'attachment; filename="deepagent-trajectory-{quote(session_id[:12], safe="")}.json"'})
        if body.event_head_sequence != snapshot.head or body.snapshot_digest != snapshot.digest:
            raise HTTPException(409, "会话内容已变化，请重新预估后确认导出")
        snapshot = await within_budget(_describe_files(ctx, request, snapshot, user_id, admin))
        if len(snapshot.files) > MAX_FILES or sum(item.size or 0 for item in snapshot.files) + len(trace) > MAX_TOTAL_BYTES:
            raise HTTPException(413, "文件数量或总大小超过导出上限")
        task = asyncio.create_task(_build(ctx, request, snapshot, user_id, trace, admin=admin, deadline=deadline))
        async def disconnected():
            while not await request.is_disconnected():
                await asyncio.sleep(.1)
        disconnect = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait((task, disconnect), return_when=asyncio.FIRST_COMPLETED)
            if task not in done:
                task.cancel()
                await task
            path, temporary, included, skipped = await task
        except BaseException:
            task.cancel()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except BaseException:
                    break
            if task.done() and not task.cancelled() and task.exception() is None:
                # Cancellation can arrive after the archive finished but before
                # its result was assigned to this request's cleanup owner.
                task.result()[1].cleanup()
            raise
        finally:
            disconnect.cancel()
            await asyncio.gather(disconnect, return_exceptions=True)
        return ArchiveResponse(path, temporary, cleanup, {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff",
            "Content-Disposition": f'attachment; filename="deepagent-trajectory-{quote(session_id[:12], safe="")}.zip"',
            "X-Deep-Agent-Export-Status": "partial" if skipped else "complete",
            "X-Deep-Agent-Export-Included-Files": str(included), "X-Deep-Agent-Export-Skipped-Files": str(skipped)})
    except (asyncio.TimeoutError, TimeoutError):
        cleanup()
        raise HTTPException(504, "导出超过时间上限，请仅导出轨迹或减少文件后重试") from None
    except BaseException:
        cleanup()
        raise
