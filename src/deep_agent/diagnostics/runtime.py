"""Bounded, best-effort diagnostic files; no business execution depends on them."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import queue
import re
import socket
import stat
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone

try:
    import fcntl
except ImportError:  # Windows is not a supported file-locking deployment target.
    fcntl = None

from deep_agent.core.paths import REPO_ROOT
from deepfos_platform.lib.context import get_request_id
from .schema import EVENTS, ISSUE_CODES, MAX_RECORD_BYTES, safe_fields
from .text import format_record

FILE_PATTERN = re.compile(r"da-[a-f0-9]{16}-(?:api|gateway)-[0-9]+-[a-f0-9]{32}-[0-9]{6}\.(?:log|jsonl)\Z")


def mirror_operational(data: bytes, level: str) -> None:
    """Mirror the exact text record without a second operational prefix."""
    from loguru import logger
    logger.opt(raw=True).log(level, data.decode())


def instance_id() -> str:
    return hashlib.sha256(socket.gethostname().encode()).hexdigest()[:16]


@dataclass(frozen=True)
class DiagnosticConfig:
    directory: Path
    instance_id: str = field(default_factory=instance_id)
    enabled: bool = True
    max_file_bytes: int = 20 * 1024 * 1024
    retention_days: int = 7
    max_directory_bytes: int = 512 * 1024 * 1024
    queue_size: int = 2048


def config_from_env() -> DiagnosticConfig:
    configured = os.environ.get("DEEP_AGENT_DIAGNOSTICS_LOG_DIR", "").strip()
    log_path = os.environ.get("DEEP_AGENT_LOG_FILE_PATH", "").strip()
    if configured:
        directory = Path(configured).expanduser()
    elif log_path:
        directory = Path(log_path).expanduser().parent / "diagnostics"
    else:
        directory = REPO_ROOT / "data" / "logs" / "diagnostics"
    if not directory.is_absolute():
        directory = REPO_ROOT / directory
    return DiagnosticConfig(directory=directory,
        enabled=os.environ.get("DEEP_AGENT_DIAGNOSTICS_ENABLED", "true").lower() not in ("0", "false", "no"))


def open_directory(directory: Path, *, create: bool = False) -> int:
    if create:
        directory.mkdir(parents=True, exist_ok=True, mode=0o750)
    # Configuration is trusted; file names are not. Pin the opened directory
    # inode and use dir_fd + O_NOFOLLOW for every subsequent file operation.
    return os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)


def regular_open(directory_fd: int, name: str, flags: int, mode: int = 0o640) -> int:
    if "/" in name or "\\" in name or name in ("", ".", ".."):
        raise ValueError("Invalid diagnostic file name")
    safe_flags = (flags & ~os.O_TRUNC) | os.O_NOFOLLOW | os.O_NONBLOCK
    if flags & os.O_CREAT and not flags & os.O_EXCL:
        # macOS openat(O_CREAT|O_NOFOLLOW) can return ENOENT when two
        # processes create the same lock file. Explicit create-or-open keeps
        # that normal startup race out of the diagnostic failure path.
        try:
            fd = os.open(name, safe_flags & ~os.O_CREAT, dir_fd=directory_fd)
        except FileNotFoundError:
            try:
                fd = os.open(name, safe_flags | os.O_EXCL, mode, dir_fd=directory_fd)
            except FileExistsError:
                fd = os.open(name, safe_flags & ~os.O_CREAT, dir_fd=directory_fd)
    else:
        fd = os.open(name, safe_flags, mode, dir_fd=directory_fd)
    st = os.fstat(fd)
    if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
        os.close(fd)
        raise OSError("Unsafe diagnostic file")
    if flags & os.O_TRUNC:
        os.ftruncate(fd, 0)
    return fd


class DiagnosticRuntime:
    def __init__(self, config: DiagnosticConfig, source: str):
        if source not in ("api", "gateway"):
            raise ValueError("Unknown diagnostic source")
        self.config = config
        self.source = source
        self.pid = os.getpid()
        self.boot_id = uuid.uuid4().hex
        self.stem = f"da-{config.instance_id}-{source}-{self.pid}-{self.boot_id}"
        self.queue: queue.Queue[bytes] = queue.Queue(maxsize=max(1, config.queue_size))
        self.stop_event = threading.Event()
        self.thread: threading.Thread | None = None
        self._state_lock = threading.Lock()
        self._submit_lock = threading.Lock()
        self._issues: dict[str, int] = {}
        self._dropped = 0
        self._last_warning = -float("inf")
        self._available = False
        self._dirfd: int | None = None
        self._filefd: int | None = None
        self._file_name: str | None = None
        self._file_bytes = 0
        self._sequence = 0
        self._record_sequence = 0
        self._last_maintenance = 0.0
        self._last_status = 0.0
        self._last_timestamp = ""

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self._consume, name="deep-agent-diagnostics", daemon=True)
        self.thread.start()

    def emit(self, event: str, *, level: str = "INFO", **fields) -> None:
        try:
            if self.stop_event.is_set() or event not in EVENTS:
                return
            with self._submit_lock:
                self._record_sequence += 1
                # Preserve the per-file ordering used by the query merge even
                # when the host clock is adjusted backwards.
                self._last_timestamp = max(self._last_timestamp,
                    datetime.now(timezone.utc).isoformat(timespec="milliseconds"))
                payload = {
                    "schema_version": 1,
                    "timestamp": self._last_timestamp,
                    "record_id": f"{self.boot_id}-{self._record_sequence:016x}",
                    "event": event, "summary": EVENTS[event],
                    "level": level if level in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL") else "INFO",
                    "source": self.source, "instance_id": self.config.instance_id,
                    "process_id": self.pid, "boot_id": self.boot_id,
                    **safe_fields({"request_id": get_request_id(), **fields}),
                }
                data = (format_record(payload) + "\n").encode()
                if len(data) > MAX_RECORD_BYTES:
                    self._issue("runtime_fault", dropped=True)
                    return
                try:
                    self.queue.put_nowait(data)
                except queue.Full:
                    self._issue("queue_full", dropped=True)
        except Exception:
            self._issue("runtime_fault", dropped=True)

    def _issue(self, code: str, *, dropped: bool = False) -> None:
        try:
            with self._state_lock:
                self._issues[code] = self._issues.get(code, 0) + 1
                self._dropped += int(dropped)
        except Exception:
            pass

    def status(self) -> dict:
        with self._state_lock:
            issues = [{"source": self.source, "code": code, "dropped_records": self._dropped}
                      for code in self._issues if code in ISSUE_CODES]
        return {"enabled": self.config.enabled, "available": self._available,
                "instance_id": self.config.instance_id, "scope": "current_instance",
                "sources": ["api", "gateway"], "source": self.source,
                "retention_days": self.config.retention_days, "issues": issues,
                "process_id": self.pid, "boot_id": self.boot_id,
                "current_file": self._file_name, "dropped_records": self._dropped,
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds")}

    def _publish_status(self) -> None:
        if self._dirfd is None:
            return
        name = self.stem + ".status.json"
        temporary = self.stem + ".status.tmp"
        fd = regular_open(self._dirfd, temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
        try:
            remaining = memoryview(json.dumps(self.status(), separators=(",", ":")).encode())
            while remaining:
                written = os.write(fd, remaining)
                if written <= 0:
                    raise OSError("Incomplete diagnostic status write")
                remaining = remaining[written:]
        finally:
            os.close(fd)
        os.replace(temporary, name, src_dir_fd=self._dirfd, dst_dir_fd=self._dirfd)
        self._last_status = time.monotonic()

    def _close_file(self) -> None:
        if self._filefd is not None:
            os.close(self._filefd)
        self._filefd = None
        self._file_name = None
        self._file_bytes = 0

    def _maintenance(self, incoming: int = 0) -> bool:
        """Caller holds the directory lock. Never delete a file locked by a writer."""
        now = time.time()
        entries = []
        total = 0
        for name in os.listdir(self._dirfd):
            if not FILE_PATTERN.fullmatch(name):
                continue
            try:
                info = os.stat(name, dir_fd=self._dirfd, follow_symlinks=False)
            except FileNotFoundError:
                continue
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                continue
            total += info.st_size
            entries.append((info.st_mtime, name, info.st_size))
        for modified, name, size in sorted(entries):
            expired = modified < now - self.config.retention_days * 86400
            if not expired and total + incoming <= self.config.max_directory_bytes:
                continue
            try:
                fd = regular_open(self._dirfd, name, os.O_RDONLY)
            except FileNotFoundError:
                continue
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    continue
                # Same descriptor is locked through unlink; no active writer can
                # become this file (writers always create unique file names).
                os.unlink(name, dir_fd=self._dirfd)
                total -= size
            finally:
                os.close(fd)
        # Status metadata is not a second event store. Bound old per-boot files.
        status_pattern = re.compile(r"da-[a-f0-9]{16}-(?:api|gateway)-[0-9]+-[a-f0-9]{32}\.status\.(?:json|tmp)\Z")
        for name in os.listdir(self._dirfd):
            if not status_pattern.fullmatch(name) or name.startswith(self.stem):
                continue
            try:
                info = os.stat(name, dir_fd=self._dirfd, follow_symlinks=False)
                if stat.S_ISREG(info.st_mode) and info.st_mtime < now - self.config.retention_days * 86400:
                    os.unlink(name, dir_fd=self._dirfd)
            except FileNotFoundError:
                # Other processes atomically replace their own status files
                # without acquiring the data-file maintenance lock.
                continue
        self._last_maintenance = time.monotonic()
        return total + incoming <= self.config.max_directory_bytes

    def _write(self, data: bytes) -> None:
        if self._dirfd is None:
            if fcntl is None:
                raise OSError("Diagnostic file locking is unavailable")
            self._dirfd = open_directory(self.config.directory, create=True)
        lockfd = regular_open(self._dirfd, ".maintenance.lock", os.O_RDWR | os.O_CREAT)
        try:
            fcntl.flock(lockfd, fcntl.LOCK_EX)
            if self._filefd is not None and self._file_bytes + len(data) > self.config.max_file_bytes:
                self._close_file()
            if not self._maintenance(len(data)):
                self._issue("capacity_exceeded", dropped=True)
                return
            if self._filefd is None:
                self._sequence += 1
                name = f"{self.stem}-{self._sequence:06d}.log"
                self._filefd = regular_open(self._dirfd, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
                fcntl.flock(self._filefd, fcntl.LOCK_EX)
                self._file_name = name
            remaining = memoryview(data)
            while remaining:
                written = os.write(self._filefd, remaining)
                if written <= 0:
                    raise OSError("Incomplete diagnostic write")
                remaining = remaining[written:]
            self._file_bytes += len(data)
            self._available = True
        finally:
            os.close(lockfd)

    def _consume(self) -> None:
        try:
            while not self.stop_event.is_set() or not self.queue.empty():
                try:
                    data = self.queue.get(timeout=0.2)
                except queue.Empty:
                    data = None
                try:
                    if data is not None:
                        try:
                            mirror_operational(data, data.decode().split(" | ", 3)[2])
                        except Exception:
                            self._issue("operational_output_failed")
                    if not self.config.enabled:
                        continue
                    if data is not None:
                        self._write(data)
                    elif self._dirfd is None:
                        if fcntl is None:
                            raise OSError("Diagnostic file locking is unavailable")
                        self._dirfd = open_directory(self.config.directory, create=True)
                        self._available = True
                    if self._dirfd is not None and time.monotonic() - self._last_maintenance >= 60:
                        lockfd = regular_open(self._dirfd, ".maintenance.lock", os.O_RDWR | os.O_CREAT)
                        try:
                            fcntl.flock(lockfd, fcntl.LOCK_EX)
                            self._maintenance()
                        finally:
                            os.close(lockfd)
                except Exception:
                    self._available = False
                    self._issue("directory_unavailable" if self._dirfd is None else "write_failed", dropped=data is not None)
                    # A partial write is never continued in the same file. The
                    # query reader can retain its last fragment as raw text.
                    self._close_file()
                finally:
                    if self.config.enabled and self._dirfd is not None and time.monotonic() - self._last_status >= 1:
                        try:
                            self._publish_status()
                        except Exception:
                            # A status-file error is not a lost data record.
                            self._issue("status_unavailable")
                            self._last_status = time.monotonic()
                    if data is not None:
                        self.queue.task_done()
                    if self._issues and time.monotonic() - self._last_warning >= 60:
                        self._last_warning = time.monotonic()
                        try:
                            print(f"DeepAgent diagnostics degraded source={self.source} dropped={self._dropped}", file=sys.stderr)
                        except Exception:
                            pass
        finally:
            self._close_file()
            try:
                self._publish_status()
            except Exception:
                pass
            if self._dirfd is not None:
                os.close(self._dirfd)
                self._dirfd = None

    def close(self, timeout: float = 2.0) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout=timeout)
            if self.thread.is_alive():
                self._issue("shutdown_incomplete")


_runtime: DiagnosticRuntime | None = None
_initialization_failed = False


def get_runtime() -> DiagnosticRuntime | None:
    return _runtime


def initialization_failed() -> bool:
    return _initialization_failed


def initialize(source: str) -> DiagnosticRuntime | None:
    global _runtime, _initialization_failed
    try:
        if _runtime is not None and _runtime.pid == os.getpid() and not _runtime.stop_event.is_set():
            return _runtime
        _runtime = DiagnosticRuntime(config_from_env(), source)
        _runtime.start()
        _initialization_failed = False
        return _runtime
    except Exception:
        _runtime = None
        _initialization_failed = True
        try:
            print("DeepAgent diagnostics initialization failed", file=sys.stderr)
        except Exception:
            pass
        return None


def shutdown() -> None:
    global _runtime
    runtime, _runtime = _runtime, None
    if runtime is not None:
        try:
            runtime.close()
        except Exception:
            pass


def emit(event: str, *, level: str = "INFO", **fields) -> None:
    try:
        if _runtime is not None:
            _runtime.emit(event, level=level, **fields)
        elif event in EVENTS:
            # Outside an initialized ASGI service, retain the original
            # operational logging path (CLI/tests). No diagnostic file I/O.
            payload = {"event": event, "summary": EVENTS[event],
                       **safe_fields({"request_id": get_request_id(), **fields}),
                       "timestamp": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                       "process_id": os.getpid(), "level": level, "source": "api",
                       "record_id": uuid.uuid4().hex, "boot_id": uuid.uuid4().hex,
                       "instance_id": instance_id()}
            mirror_operational((format_record(payload) + "\n").encode(), level)
    except Exception:
        pass


def observe_lifespan(wrapped, source: str):
    """Observe an ASGI lifespan without taking over its cleanup or exceptions."""
    @asynccontextmanager
    async def observed(app):
        initialize(source)
        emit("service_starting")
        started = False
        try:
            async with wrapped(app):
                started = True
                emit("service_started")
                yield
        except BaseException as exc:
            emit("service_shutdown_failed" if started else "service_initialization_failed",
                 level="ERROR", error_type=type(exc).__name__)
            raise
        else:
            emit("service_stopped")
        finally:
            shutdown()
    return observed
