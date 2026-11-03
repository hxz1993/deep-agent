"""Bounded, cancellable image/PDF normalization in isolated subprocesses."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import tempfile
from weakref import WeakKeyDictionary, ref

from .limits import (
    MAX_IMAGE_SOURCE_BYTES, MAX_OUTPUT_BYTES, MAX_OUTPUT_EDGE, MAX_OUTPUT_PIXELS,
    MAX_PDF_SOURCE_BYTES, MAX_PROTOCOL_BYTES, MAX_RENDER_WORKERS,
    MAX_SOURCE_IMAGES, MAX_WAITING_RENDERS, RENDER_TIMEOUT,
)

_WORKER = Path(__file__).with_name("worker.py")
_FORMATS = {"pdf", "png", "jpeg", "webp", "gif"}
_ERROR_CODE = re.compile(r"[a-z][a-z0-9_]{0,63}\Z")


class ImageRenderError(ValueError):
    """A bounded error code suitable for a model-visible tool failure."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(f"Image preparation failed ({code})")


def detect_format(data: bytes) -> str:
    """Inspect content signatures, never filename extensions or caller MIME."""
    if not isinstance(data, bytes):
        raise ImageRenderError("invalid_source")
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    # PDF readers permit a small header prefix; PDFium still verifies validity.
    if b"%PDF-" in data[:1024]:
        return "pdf"
    raise ImageRenderError("unsupported_format")


def _pages_for(fmt: str, pages: list[int] | None) -> list[int] | None:
    if fmt != "pdf":
        if pages is not None:
            raise ImageRenderError("pages_for_image")
        return None
    if pages is None:
        return [1]
    if not isinstance(pages, list) or not pages or any(type(n) is not int or n < 1 or n > 2**31 - 1 for n in pages):
        raise ImageRenderError("invalid_pages")
    unique = list(dict.fromkeys(pages))
    if len(unique) > MAX_SOURCE_IMAGES:
        raise ImageRenderError("page_limit")
    return unique


class _Capacity:
    def __init__(self):
        self.slots = asyncio.Semaphore(MAX_RENDER_WORKERS)
        self.waiting = 0

    async def acquire(self):
        if not self.slots.locked():
            await self.slots.acquire()
            return
        if self.waiting >= MAX_WAITING_RENDERS:
            raise ImageRenderError("render_queue_full")
        self.waiting += 1
        try:
            await self.slots.acquire()
        finally:
            self.waiting -= 1


_CAPACITIES: WeakKeyDictionary = WeakKeyDictionary()


def _capacity() -> _Capacity:
    loop = asyncio.get_running_loop()
    existing = _CAPACITIES.get(loop)
    capacity = existing() if existing is not None else None
    if capacity is None:
        capacity = _Capacity()
        # A waiting semaphore can retain its loop. A weak value also lets
        # independent test/server event loops be collected normally.
        _CAPACITIES[loop] = ref(capacity)
    return capacity


async def _finish_after_cancel(task):
    """Recover a spawned Process or finish a filesystem write before cleanup."""
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
        try:
            task.result()
        except Exception:
            # A coincident write failure must not turn cancellation into a
            # model-visible failure and prompt an unintended tool retry.
            pass
        raise


async def _kill_and_reap(process):
    if process.returncode is None:
        try:
            process.kill()
        except ProcessLookupError:
            pass
    cleanup = asyncio.create_task(process.communicate())
    while not cleanup.done():
        try:
            await asyncio.shield(cleanup)
        except asyncio.CancelledError:
            continue
    cleanup.result()


def _write_source(root: Path, data: bytes):
    descriptor = os.open(root / "source.bin", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(data)


async def _communicate(process, payload: bytes) -> bytes:
    process.stdin.write(payload)
    await process.stdin.drain()
    process.stdin.close()
    # readexactly bounds memory while still waiting for EOF; read(n) alone may
    # return an incomplete JSON prefix as soon as the first pipe chunk arrives.
    try:
        await process.stdout.readexactly(MAX_PROTOCOL_BYTES + 1)
    except asyncio.IncompleteReadError as exc:
        stdout = exc.partial
    else:
        raise ImageRenderError("worker_protocol_error")
    await process.wait()
    if process.returncode != 0:
        raise ImageRenderError("worker_failed")
    return stdout


def _read_image(root: Path, item: dict, used_paths: set[str]) -> dict:
    expected = {"page", "original_width", "original_height", "width", "height", "media_type", "sha256", "scaled", "gif_first_frame", "path", "size"}
    if not isinstance(item, dict) or set(item) != expected:
        raise ValueError
    name = item["path"]
    if not isinstance(name, str) or not re.fullmatch(r"image-[0-7]\.(?:png|jpeg)", name) or name in used_paths:
        raise ValueError
    used_paths.add(name)
    for key in ("original_width", "original_height", "width", "height", "size"):
        if type(item[key]) is not int or not 1 <= item[key] <= 10**12:
            raise ValueError
    if (max(item["width"], item["height"]) > MAX_OUTPUT_EDGE or item["width"] * item["height"] > MAX_OUTPUT_PIXELS
            or item["size"] > MAX_OUTPUT_BYTES or type(item["scaled"]) is not bool or type(item["gif_first_frame"]) is not bool):
        raise ValueError
    media_type = item["media_type"]
    if media_type not in {"image/png", "image/jpeg"} or not isinstance(item["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"]):
        raise ValueError
    path = root / "outputs" / name
    # Open nonblocking so an unexpected FIFO cannot freeze the API loop
    # before fstat has a chance to reject it as a non-regular output.
    descriptor = os.open(path, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_NOFOLLOW", 0))
    with os.fdopen(descriptor, "rb") as stream:
        metadata = os.fstat(stream.fileno())
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1 or metadata.st_size != item["size"] or stat.S_IMODE(metadata.st_mode) != 0o600:
            raise ValueError
        data = stream.read(MAX_OUTPUT_BYTES + 1)
    if len(data) != item["size"] or hashlib.sha256(data).hexdigest() != item["sha256"]:
        raise ValueError
    if not data.startswith(b"\x89PNG\r\n\x1a\n" if media_type == "image/png" else b"\xff\xd8\xff"):
        raise ValueError
    return {key: value for key, value in item.items() if key not in {"path", "size"}} | {"data": data}


def _decode_result(stdout: bytes, root: Path, fmt: str, pages: list[int] | None) -> dict:
    try:
        result = json.loads(stdout)
        if isinstance(result, dict) and set(result) == {"error"} and isinstance(result["error"], str) and _ERROR_CODE.fullmatch(result["error"]):
            raise ImageRenderError(result["error"])
        if not isinstance(result, dict) or set(result) != {"format", "page_count", "images", "errors"} or result["format"] != fmt or fmt not in _FORMATS:
            raise ValueError
        if fmt == "pdf":
            if type(result["page_count"]) is not int or not 1 <= result["page_count"] <= 2**31 - 1:
                raise ValueError
        elif result["page_count"] is not None:
            raise ValueError
        images, errors = result["images"], result["errors"]
        if not isinstance(images, list) or len(images) > MAX_SOURCE_IMAGES or not isinstance(errors, list) or len(errors) > MAX_SOURCE_IMAGES:
            raise ValueError
        seen, used_paths, prepared = set(), set(), []
        wanted = set(pages) if pages is not None else {None}
        for item in images:
            number = item.get("page") if isinstance(item, dict) else False
            if (number is not None and type(number) is not int) or number not in wanted or number in seen or (fmt == "pdf" and number > result["page_count"]):
                raise ValueError
            seen.add(number)
            prepared.append(_read_image(root, item, used_paths))
        for error in errors:
            if not isinstance(error, dict) or set(error) != {"page", "error"} or not isinstance(error["error"], str) or not _ERROR_CODE.fullmatch(error["error"]):
                raise ValueError
            number = error["page"]
            if (number is not None and type(number) is not int) or number not in wanted or number in seen:
                raise ValueError
            seen.add(number)
        if seen != wanted:
            raise ValueError
        return {**result, "images": prepared}
    except ImageRenderError:
        raise
    except (ValueError, TypeError, KeyError, OSError, OverflowError, RecursionError) as exc:
        raise ImageRenderError("worker_protocol_error") from exc


async def render_source(data: bytes, pages: list[int] | None = None) -> dict:
    """Prepare native images and selected PDF pages without touching FileStore.

    The source timeout includes queueing and process startup. The parent tool
    owns call-wide/Run-wide byte budgets and file authorization. All filesystem
    and process resources here belong to this one source invocation.
    """
    fmt = detect_format(data)
    if len(data) > (MAX_PDF_SOURCE_BYTES if fmt == "pdf" else MAX_IMAGE_SOURCE_BYTES):
        raise ImageRenderError("source_size_limit")
    selection = _pages_for(fmt, pages)
    capacity = _capacity()
    acquired = False
    process = None
    directory = None
    try:
        async with asyncio.timeout(RENDER_TIMEOUT):
            await capacity.acquire()
            acquired = True
            directory = tempfile.TemporaryDirectory(prefix="TMP_to_delete-view-image-")
            root = Path(directory.name)
            root.chmod(0o700)
            (root / "outputs").mkdir(mode=0o700)
            await _finish_after_cancel(asyncio.create_task(asyncio.to_thread(_write_source, root, data)))
            spawning = asyncio.create_task(asyncio.create_subprocess_exec(
                sys.executable, "-I", "-B", str(_WORKER),
                cwd=root, env={"LANG": "C.UTF-8", "LC_ALL": "C.UTF-8"},
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            ))
            try:
                process = await asyncio.shield(spawning)
            except asyncio.CancelledError:
                while not spawning.done():
                    try:
                        await asyncio.shield(spawning)
                    except asyncio.CancelledError:
                        continue
                    except Exception:
                        break
                try:
                    process = spawning.result()
                except Exception:
                    pass
                raise
            payload = json.dumps({"format": fmt, "pages": selection}).encode("ascii")
            stdout = await _communicate(process, payload)
            return _decode_result(stdout, root, fmt, selection)
    except TimeoutError as exc:
        raise ImageRenderError("render_timeout") from exc
    except (OSError, BrokenPipeError) as exc:
        raise ImageRenderError("worker_unavailable") from exc
    finally:
        try:
            if process is not None:
                await _kill_and_reap(process)
        finally:
            try:
                if directory is not None:
                    directory.cleanup()
            finally:
                if acquired:
                    capacity.slots.release()
