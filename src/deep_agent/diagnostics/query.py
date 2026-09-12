"""Bounded, read-only diagnostics access. No conversation/Event Store access."""
from __future__ import annotations

import hashlib
import heapq
import json
import os
import re
import secrets
import stat
import threading
import time
from collections import OrderedDict
from datetime import datetime, timedelta, timezone

MAX_SCAN_BYTES = 32 * 1024 * 1024
MAX_SCAN_SECONDS = 2.0
MAX_FILES = 4096
MAX_LINE_BYTES = 64 * 1024
MAX_CURSOR_ENTRIES = 128
MAX_CURSOR_BYTES = 16 * 1024 * 1024
CURSOR_TTL_SECONDS = 15 * 60
_SLOTS = threading.BoundedSemaphore(2)
_NAME = re.compile(r"da-([a-f0-9]{16})-(api|gateway)-(\d+)-([a-f0-9]{32})-(\d{6})\.(?:log|jsonl)\Z")
_STATUS = re.compile(r"da-([a-f0-9]{16})-(api|gateway)-(\d+)-([a-f0-9]{32})\.status\.json\Z")


class QueryError(ValueError):
    def __init__(self, code: str, status_code: int = 409):
        super().__init__(code)
        self.code = code
        self.status_code = status_code


class _MetadataBudgetExceeded(Exception):
    pass


def _utc(value: str | datetime) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
        if parsed.tzinfo is None or parsed.utcoffset() != timedelta(0):
            raise ValueError("UTC required")
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, AttributeError) as exc:
        raise QueryError("invalid_time_range", 422) from exc


def _iso(value: datetime) -> str:
    return value.isoformat().replace("+00:00", "Z")


def _directory(path) -> int:
    """Share writer's trusted-config directory boundary, never a query path.

    OS aliases in configured parent directories (macOS /var) are legitimate.
    The actual log directory and every file still require O_NOFOLLOW.
    """
    from .runtime import open_directory
    return open_directory(path)


def _regular(directory: int, name: str) -> int:
    fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    info = os.fstat(fd)
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        os.close(fd)
        raise QueryError("unsafe_log_file")
    return fd


class _CursorCache:
    """Private, bounded snapshots; opaque references keep GET URLs short.

    Entries are immutable JSON bytes, not mutable traversal dictionaries. A
    retry of the same token therefore always starts at the same scan position.
    The cache is deliberately process-local and never persists to disk or SQL.
    """
    def __init__(self, *, max_entries=MAX_CURSOR_ENTRIES, max_bytes=MAX_CURSOR_BYTES,
                 ttl=CURSOR_TTL_SECONDS, clock=time.monotonic):
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self.ttl = ttl
        self.clock = clock
        self.entries = OrderedDict()
        self.used_bytes = 0
        self._lock = threading.Lock()

    def _remove(self, token):
        _, _, cost = self.entries.pop(token)
        self.used_bytes -= cost

    def _prune(self, now):
        for token, (expires, _, _) in list(self.entries.items()):
            if expires <= now:
                self._remove(token)

    def put(self, payload: dict) -> str:
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        # Include a conservative fixed allowance for token and entry metadata;
        # only the serialized body is retained, not a second decoded snapshot.
        cost = len(body) + 512
        if self.max_entries < 1 or cost > self.max_bytes:
            raise QueryError("diagnostics_cursor_capacity_exceeded", 503)
        with self._lock:
            now = self.clock()
            self._prune(now)
            while self.entries and (len(self.entries) >= self.max_entries or self.used_bytes + cost > self.max_bytes):
                self._remove(next(iter(self.entries)))
            token = secrets.token_urlsafe(32)
            while token in self.entries:
                token = secrets.token_urlsafe(32)
            self.entries[token] = (now + self.ttl, body, cost)
            self.used_bytes += cost
        return token

    def get(self, token: str) -> dict:
        if not isinstance(token, str) or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None:
            raise QueryError("invalid_or_expired_cursor")
        with self._lock:
            self._prune(self.clock())
            entry = self.entries.get(token)
            if entry is None:
                raise QueryError("invalid_or_expired_cursor")
            self.entries.move_to_end(token)
            body = entry[1]
        return json.loads(body)


_CURSORS = _CursorCache()


def _encode(payload: dict) -> str:
    return _CURSORS.put(payload)


def _decode(cursor: str) -> dict:
    return _CURSORS.get(cursor)


def _snapshot(directory: int, instance: str, source: str | None, budget) -> tuple[list[dict], bool]:
    files = []
    overflow = False
    # scandir is incremental; never materialize arbitrary directory contents.
    with os.scandir(directory) as entries:
        for entry in entries:
            budget.check_metadata()
            match = _NAME.fullmatch(entry.name)
            if not match or match[1] != instance or (source and match[2] != source):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except OSError as exc:
                raise QueryError("log_snapshot_changed") from exc
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise QueryError("unsafe_log_file")
            if len(files) >= MAX_FILES:
                overflow = True
                break
            files.append({"name": entry.name, "dev": info.st_dev, "ino": info.st_ino,
                          "size": info.st_size, "mtime": info.st_mtime_ns})
    files.sort(key=lambda item: (item["mtime"], item["name"]), reverse=True)
    return files, overflow


def _validate(directory: int, files: list[dict], instance: str, budget=None) -> None:
    if not isinstance(files, list) or len(files) > MAX_FILES:
        raise QueryError("invalid_or_expired_cursor")
    for item in files:
        if budget is not None:
            budget.check_metadata()
        match = _NAME.fullmatch(item["name"])
        if not match or match[1] != instance:
            raise QueryError("invalid_or_expired_cursor")
        try:
            info = os.stat(item["name"], dir_fd=directory, follow_symlinks=False)
        except OSError as exc:
            raise QueryError("log_snapshot_changed") from exc
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or
                (info.st_dev, info.st_ino) != (item["dev"], item["ino"]) or info.st_size < item["size"]):
            raise QueryError("log_snapshot_changed")


class _Budget:
    def __init__(self):
        self.started = time.monotonic()
        self.scanned = 0

    def remaining(self) -> int:
        if time.monotonic() - self.started >= MAX_SCAN_SECONDS:
            return 0
        return max(0, MAX_SCAN_BYTES - self.scanned)

    def check_metadata(self):
        if time.monotonic() - self.started >= MAX_SCAN_SECONDS:
            raise _MetadataBudgetExceeded()


def _lines(fd: int, position: int, budget: _Budget, *, skipping=False):
    """Reverse lines with bounded buffers; yielded offset resumes before line."""
    read_at = position
    buffer = b""
    oversized = skipping
    line_end = position
    while read_at > 0:
        count = min(64 * 1024, read_at, budget.remaining())
        if count <= 0:
            return
        chunk = os.pread(fd, count, read_at - count)
        budget.scanned += len(chunk)
        if len(chunk) != count:
            raise QueryError("log_snapshot_changed")
        read_at -= count
        buffer = chunk + buffer
        while (split := buffer.rfind(b"\n")) >= 0:
            line, buffer = buffer[split + 1:], buffer[:split]
            offset = read_at + split
            yield (None if oversized or len(line) > MAX_LINE_BYTES else line), offset, offset + 1, line_end, False
            line_end = offset
            oversized = False
            if not budget.remaining():
                return
        if len(buffer) > MAX_LINE_BYTES:
            buffer = b""
            oversized = True
            # A malformed huge line must not trap every continuation at the
            # same offset. Remember to discard its prefix until a delimiter.
            yield None, read_at, read_at, read_at, True
    if buffer or oversized:
        yield (None if oversized else buffer), 0, 0, line_end, False


def _candidate(raw, *, instance, file, offset, filters, start_at, end_at):
    from .text import parse_record
    if not raw or len(raw) > MAX_LINE_BYTES:
        return None
    line = raw.decode("utf-8", errors="replace")
    record = parse_record(line)
    if record:
        if record.get("instance_id") != instance or record.get("source") != _NAME.fullmatch(file["name"])[2]:
            return None
        if not start_at <= _utc(record["timestamp"]) <= end_at:
            return None
    else:
        # Reading identity is distinct from log content. Never invent a time,
        # level or source for an opaque legacy/malformed line.
        identity = f'{file["dev"]}:{file["ino"]}:{offset}'
        record = {"raw": line, "record_id": hashlib.sha256(identity.encode()).hexdigest(),
                  "boot_id": _NAME.fullmatch(file["name"])[4], "instance_id": instance}
    if any(value and record.get(key) != value for key, value in filters.items()
           if key in ("source", "level", "session_id", "run_id")):
        return None
    if filters["q"] and filters["q"].casefold() not in line.casefold():
        return None
    return record


def _order(record, file):
    # Opaque lines have no claimed event time; mtime is used only internally to
    # provide deterministic pagination among files, never returned as timestamp.
    return _utc(record["timestamp"]).timestamp() if "timestamp" in record else file["mtime"] / 1e9


def _file(directory, item):
    fd = _regular(directory, item["name"])
    info = os.fstat(fd)
    if (info.st_dev, info.st_ino) != (item["dev"], item["ino"]) or info.st_size < item["size"]:
        os.close(fd)
        raise QueryError("log_snapshot_changed")
    return fd


def query_logs(config, *, start=None, end=None, source=None, level=None,
               session_id=None, run_id=None, q=None, limit=200, cursor=None) -> dict:
    if not _SLOTS.acquire(blocking=False):
        raise QueryError("diagnostics_query_busy", 503)
    try:
        return _query(config, start=start, end=end, source=source, level=level,
                      session_id=session_id, run_id=run_id, q=q, limit=limit, cursor=cursor)
    finally:
        _SLOTS.release()


def _query(config, *, start, end, source, level, session_id, run_id, q, limit, cursor):
    budget = _Budget()
    previous = _decode(cursor) if cursor else None
    now = datetime.now(timezone.utc)
    previous_filters = previous.get("filters", {}) if previous else {}
    end_at = _utc(end or previous_filters.get("end") or now)
    try:
        start_at = _utc(start or previous_filters.get("start") or (end_at - timedelta(hours=24)))
    except OverflowError as exc:
        raise QueryError("invalid_time_range", 422) from exc
    if start_at >= end_at or source not in (None, "api", "gateway"):
        raise QueryError("invalid_filters", 422)
    if not isinstance(limit, int) or not 1 <= limit <= 500:
        raise QueryError("invalid_limit", 422)
    if any(value is not None and (not isinstance(value, str) or len(value) > 512)
           for value in (session_id, run_id, q, level)):
        raise QueryError("invalid_filters", 422)
    level = level.upper() if level else None
    if level not in (None, "DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        raise QueryError("invalid_level", 422)
    filters = {"start": _iso(start_at), "end": _iso(end_at), "source": source, "level": level,
               "session_id": session_id, "run_id": run_id, "q": q}
    instance = config.instance_id
    if previous and (previous.get("instance") != instance or previous_filters != filters):
        raise QueryError("cursor_filter_mismatch")
    if not config.enabled:
        raise QueryError("diagnostics_disabled", 503)
    try:
        directory = _directory(config.directory)
    except OSError as exc:
        raise QueryError("log_snapshot_changed" if previous else "diagnostics_unavailable",
                         409 if previous else 503) from exc
    try:
        if previous:
            files, overflow = previous["files"], previous.get("overflow", False)
            states = previous["states"]
        else:
            files, overflow = _snapshot(directory, instance, source, budget)
            states = [{"pos": item["size"], "head": None, "skip": False} for item in files]
        if overflow:
            # No claim about newest records is safe without every source head.
            # Ask the caller to narrow source / resolve the file-count issue.
            return {"items": [], "next_cursor": None, "partial": True, "scanned_bytes": 0,
                    "instance_id": instance, "range": {"start": filters["start"], "end": filters["end"]}}
        _validate(directory, files, instance, budget)
        items = []
        # Each frontier head is an authenticated byte range, not log contents.
        # A bounded scan can resume building the frontier without rereading all
        # earlier files or pretending a partial source is globally newest.
        while len(items) < limit and budget.remaining():
            for item, state in zip(files, states, strict=True):
                if state["head"] is not None or state["pos"] == 0:
                    continue
                fd = _file(directory, item)
                try:
                    for raw, offset, line_start, line_end, skipping in _lines(
                            fd, state["pos"], budget, skipping=state.get("skip", False)):
                        state["pos"] = offset
                        state["skip"] = skipping
                        record = _candidate(raw, instance=instance, file=item, offset=line_start, filters=filters,
                                            start_at=start_at, end_at=end_at)
                        if record:
                            state["head"] = [line_start, line_end, _order(record, item)]
                            break
                finally:
                    os.close(fd)
                _validate(directory, [item], instance)
                if not budget.remaining():
                    break
            if any(state["head"] is None and state["pos"] > 0 for state in states):
                break
            heap = [(-state["head"][2], i) for i, state in enumerate(states) if state["head"]]
            if not heap:
                break
            heapq.heapify(heap)
            _, index = heapq.heappop(heap)
            head = states[index]["head"]
            size = head[1] - head[0]
            if budget.remaining() < size:
                break
            fd = _file(directory, files[index])
            try:
                raw = os.pread(fd, size, head[0])
                budget.scanned += len(raw)
            finally:
                os.close(fd)
            _validate(directory, [files[index]], instance)
            record = _candidate(raw, instance=instance, file=files[index], offset=head[0], filters=filters,
                                start_at=start_at, end_at=end_at)
            if record is None or _order(record, files[index]) != head[2]:
                raise QueryError("log_snapshot_changed")
            items.append(record)
            states[index]["head"] = None
        has_more = any(state["head"] is not None or state["pos"] > 0 for state in states)
        next_cursor = _encode({"instance": instance, "filters": filters, "files": files,
                               "states": states, "overflow": overflow}) if has_more else None
        return {"items": items, "next_cursor": next_cursor, "partial": bool(overflow or (has_more and len(items) < limit)),
                "scanned_bytes": budget.scanned, "instance_id": instance,
                "range": {"start": filters["start"], "end": filters["end"]}}
    except _MetadataBudgetExceeded:
        # The directory/snapshot identity could not be established within the
        # deadline. Do not expose a subset as complete or mint a false cursor.
        return {"items": [], "next_cursor": cursor, "partial": True,
                "scanned_bytes": budget.scanned, "instance_id": instance,
                "range": {"start": filters["start"], "end": filters["end"]}}
    except OSError as exc:
        raise QueryError("log_snapshot_changed") from exc
    finally:
        os.close(directory)


def logs_status(config, runtime=None) -> dict:
    if not _SLOTS.acquire(blocking=False):
        raise QueryError("diagnostics_query_busy", 503)
    try:
        return _logs_status(config, runtime)
    finally:
        _SLOTS.release()


def _logs_status(config, runtime=None) -> dict:
    from .runtime import initialization_failed
    budget = _Budget()
    result = {"enabled": config.enabled, "available": False, "instance_id": config.instance_id,
              "scope": "current_instance", "sources": ["api", "gateway"],
              "retention_days": config.retention_days, "issues": []}
    if initialization_failed():
        result["issues"].append({"source": "api", "code": "runtime_fault"})
    if not config.enabled:
        return result
    try:
        directory = _directory(config.directory)
    except OSError:
        result["issues"].append({"source": "api", "code": "diagnostics_unavailable"})
        return result
    try:
        result["available"] = True
        count = 0
        log_count = 0
        with os.scandir(directory) as entries:
            for entry in entries:
                if not budget.remaining():
                    result["issues"].append({"source": "api", "code": "status_unavailable"})
                    break
                log_match = _NAME.fullmatch(entry.name)
                if log_match and log_match[1] == config.instance_id:
                    log_count += 1
                    if log_count == MAX_FILES + 1:
                        result["issues"].append({"source": log_match[2], "code": "file_limit_exceeded"})
                        break
                match = _STATUS.fullmatch(entry.name)
                if not match or match[1] != config.instance_id:
                    continue
                count += 1
                if count > MAX_FILES:
                    result["issues"].append({"source": "api", "code": "file_limit_exceeded"})
                    break
                try:
                    fd = _regular(directory, entry.name)
                    try:
                        raw = os.read(fd, min(MAX_LINE_BYTES + 1, budget.remaining()))
                        budget.scanned += len(raw)
                    finally:
                        os.close(fd)
                    if len(raw) > MAX_LINE_BYTES:
                        raise ValueError()
                    status = json.loads(raw)
                    if not isinstance(status, dict):
                        raise ValueError()
                    if status.get("instance_id") != config.instance_id or status.get("source") != match[2]:
                        continue
                    _issues(result, status, match[2])
                except (OSError, ValueError, TypeError, OverflowError, RecursionError):
                    result["issues"].append({"source": match[2], "code": "invalid_status_file"})
        if runtime is not None:
            local = runtime.status()
            _issues(result, local, local.get("source", "api"))
        result["issues"] = list({json.dumps(issue, sort_keys=True): issue for issue in result["issues"]}.values())
        return result
    finally:
        os.close(directory)


def _issues(result, status, source):
    from .schema import ISSUE_CODES
    if source not in ("api", "gateway"):
        return
    issues = status.get("issues", [])
    if not isinstance(issues, list):
        issues = []
    for issue in issues[:100]:
        code = issue.get("code") if isinstance(issue, dict) else issue
        if isinstance(code, str) and code in ISSUE_CODES:
            result["issues"].append({"source": source, "code": code})
    dropped = status.get("dropped_records", 0)
    if isinstance(dropped, int) and not isinstance(dropped, bool) and 0 < dropped <= 10**15:
        result["issues"].append({"source": source, "code": "dropped_records", "dropped_records": dropped})
