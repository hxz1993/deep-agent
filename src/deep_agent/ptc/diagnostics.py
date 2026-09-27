"""Bounded, model-private diagnostics for one CodeMode execution."""
from __future__ import annotations

import contextvars
import json
import math
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import islice
from typing import Any

MAX_SUMMARY_BYTES = 8 * 1024
MAX_STRING_BYTES = 2 * 1024
MAX_DIAGNOSTICS_BYTES = 128 * 1024
MAX_ITEMS = 20
MAX_DEPTH = 6
_SECRET_KEYS = frozenset({
    "api_key", "apikey", "token", "access_token", "refresh_token", "authorization",
    "cookie", "secret", "password", "headers", "request_headers", "provider_details",
    "extra_identity_context", "base64_content", "payload_string", "file_content",
})
_SECRET_TEXT = re.compile(r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization|cookie)\s*[:=]\s*[^\s,;]+")
_BASE64_TEXT = re.compile(r"[A-Za-z0-9+/]{256,}={0,2}")
_FILE_TOKEN = re.compile(r"deepagent-file-data://[A-Za-z0-9]+")


def _bytes(value: Any) -> int:
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8"))


def _bounded_text(value: str, limit: int = 128) -> str:
    return value[:limit].encode("utf-8")[:limit].decode("utf-8", errors="ignore")


def _short_text(value: str) -> tuple[str, bool]:
    # Work only on a bounded prefix; even redaction must not scan a huge payload.
    prefix = value[:MAX_STRING_BYTES * 2]
    if len(value) >= 256 and _BASE64_TEXT.fullmatch(prefix):
        return "[base64-redacted]", True
    prefix = _FILE_TOKEN.sub("[file-data-token]", prefix)
    prefix = _SECRET_TEXT.sub(lambda match: match.group(1) + "=[redacted]", prefix)
    prefix = _BASE64_TEXT.sub("[base64-redacted]", prefix)
    encoded = prefix.encode("utf-8")
    if len(encoded) > MAX_STRING_BYTES:
        prefix = encoded[:MAX_STRING_BYTES].decode("utf-8", errors="ignore")
    return prefix, len(value) > len(prefix)


def _project(value: Any, depth: int, seen: set[int], flags: set[str], remaining: list[int]) -> Any:
    remaining[0] -= 1
    if remaining[0] < 0:
        flags.add("truncated")
        return {"type": type(value).__name__, "truncated": True}
    if depth >= MAX_DEPTH:
        flags.add("truncated")
        return {"type": type(value).__name__, "truncated": True}
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, str):
        text, truncated = _short_text(value)
        if "[redacted]" in text or "[base64-redacted]" in text or "[file-data-token]" in text:
            flags.add("redacted")
        if truncated:
            flags.add("truncated")
            return {"type": "string", "length": len(value), "preview": text, "truncated": True}
        return text
    if isinstance(value, (bytes, bytearray, memoryview)):
        flags.add("redacted")
        return {"type": "bytes", "byte_size": len(value), "redacted": True}
    if type(value) in (dict, list, tuple):
        identity = id(value)
        if identity in seen:
            flags.add("truncated")
            return {"type": type(value).__name__, "circular": True}
        seen.add(identity)
        try:
            if type(value) is dict:
                result = {}
                for key, child in islice(value.items(), MAX_ITEMS):
                    if not isinstance(key, str):
                        flags.add("redacted")
                        continue
                    short_key, _ = _short_text(key)
                    if key.lower() in _SECRET_KEYS:
                        result[short_key] = "[redacted]"
                        flags.add("redacted")
                    else:
                        result[short_key] = _project(child, depth + 1, seen, flags, remaining)
                if len(value) > MAX_ITEMS:
                    result["__remaining_fields__"] = len(value) - MAX_ITEMS
                    flags.add("truncated")
                return result
            result = [_project(item, depth + 1, seen, flags, remaining) for item in islice(value, MAX_ITEMS)]
            if len(value) > MAX_ITEMS:
                result.append({"remaining_items": len(value) - MAX_ITEMS})
                flags.add("truncated")
            return result
        finally:
            seen.remove(identity)
    flags.add("redacted")
    return {"type": type(value).__name__, "unavailable": True}


def summary(value: Any, *, sensitive_values: frozenset[str] = frozenset()) -> dict[str, Any]:
    """Return a bounded copy; never stringify or hash arbitrary original objects."""
    flags: set[str] = set()
    projected = _project(value, 0, set(), flags, [200])
    # Opaque file bytes may be embedded in a plain string. Replace known values
    # after the bounded projection, without traversing the original object again.
    if sensitive_values:
        def scrub(node: Any) -> Any:
            if isinstance(node, str):
                for secret in sensitive_values:
                    if secret and secret in node:
                        flags.add("redacted")
                        return "[redacted]"
                return node
            if isinstance(node, list):
                return [scrub(item) for item in node]
            if isinstance(node, dict):
                return {key: scrub(item) for key, item in node.items()}
            return node
        projected = scrub(projected)
    if _bytes(projected) > MAX_SUMMARY_BYTES:
        flags.add("truncated")
        projected = {"type": type(value).__name__, "truncated": True, "reason": "summary_budget"}
    return {"value": projected, "state": "redacted" if "redacted" in flags else "truncated" if flags else "empty" if value is None else "captured"}


@dataclass
class Step:
    call_id: str
    tool_name: str
    started: float | None = None
    ended: float | None = None
    status: str = "unknown"
    input: dict[str, Any] | None = None
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None


@dataclass
class Collector:
    parent_id: str
    run_id: str | None
    session_id: str | None
    sensitive_values: frozenset[str]
    started: float = field(default_factory=time.perf_counter)
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    steps: dict[str, Step] = field(default_factory=dict)
    execution: Any = None
    final_status: str = "running"
    ended: float | None = None
    capture_error: bool = False

    def add_sensitive_text(self, value: str) -> None:
        if value:
            # A prefix also covers a long file body passed to a later tool as
            # part of a larger string, without retaining another full copy.
            self.sensitive_values = self.sensitive_values | {value[:64]}

    def begin(self, call_id: str | None, tool_name: str, args: Any) -> None:
        if not call_id:
            return
        try:
            step = self.steps.setdefault(call_id, Step(call_id, tool_name))
            step.started = time.perf_counter()
            step.input = summary(args, sensitive_values=self.sensitive_values)
        except Exception:
            self.capture_error = True

    def end(self, call_id: str | None, status: str, result: Any = None, error: Any = None) -> None:
        if not call_id:
            return
        step = self.steps.get(call_id)
        if step is None:
            return
        try:
            step.ended = time.perf_counter()
            step.status = status
            if status == "completed":
                step.output = summary(result, sensitive_values=self.sensitive_values)
            elif error is not None:
                step.error = summary(error, sensitive_values=self.sensitive_values)
        except Exception:
            self.capture_error = True

    def merge_official(self) -> None:
        execution = self.execution
        if execution is None:
            return
        for call_id, call in execution.nested_calls.items():
            step = self.steps.setdefault(call_id, Step(call_id, call.tool_name))
            if step.input is None:
                step.input = summary(call.args, sensitive_values=self.sensitive_values)
            returned = execution.nested_returns.get(call_id)
            if returned is not None:
                step.status = {"denied": "denied", "failed": "failed", "interrupted": "interrupted"}.get(
                    returned.outcome, step.status if step.status in {"failed", "denied", "interrupted"} else "completed",
                )
                if step.output is None and step.status == "completed":
                    step.output = summary(returned.content, sensitive_values=self.sensitive_values)
                if step.status in {"denied", "failed", "interrupted"} and step.error is None:
                    step.error = summary(returned.content, sensitive_values=self.sensitive_values)
            elif step.status == "unknown":
                step.status = "interrupted" if self.final_status == "interrupted" else "failed"

    def finish(self, status: str) -> None:
        self.final_status = status
        self.ended = time.perf_counter()
        try:
            self.merge_official()
        except Exception:
            self.capture_error = True

    def safe_document(self) -> dict[str, Any]:
        """Diagnostic failure must not change the surrounding tool outcome."""
        try:
            document = self.document()
            if self.capture_error:
                document["completeness"] = "incomplete"
                document["truncation"]["capture_error"] = True
            return document
        except Exception:
            return {
                "version": 1,
                "session_id": self.session_id,
                "run_id": self.run_id,
                "call_id": self.parent_id,
                "summary": {"status": self.final_status, "call_count": 0, "failed_count": 0},
                "completeness": "incomplete",
                "truncation": {"capture_error": True},
                "steps": [],
            }

    def document(self) -> dict[str, Any]:
        self.merge_official()
        steps = sorted(self.steps.values(), key=lambda step: (
            step.started if step.started is not None else float("inf"),
            int(step.call_id.rsplit("__", 1)[-1]) if step.call_id.rsplit("__", 1)[-1].isdigit() else 0,
        ))
        intervals = sorted((step.started, step.ended) for step in steps if step.started is not None and step.ended is not None)
        covered = 0.0
        if intervals:
            left, right = intervals[0]
            for start, end in intervals[1:]:
                if start <= right:
                    right = max(right, end)
                else:
                    covered += right - left
                    left, right = start, end
            covered += right - left
        result = {
            "version": 1,
            "session_id": _bounded_text(self.session_id) if self.session_id else None,
            "run_id": _bounded_text(self.run_id) if self.run_id else None,
            "call_id": _bounded_text(self.parent_id),
            "started_at": self.started_at,
            "summary": {
                "status": self.final_status,
                "duration_ms": round(((self.ended or time.perf_counter()) - self.started) * 1000, 3),
                "external_coverage_ms": round(covered * 1000, 3),
                "call_count": len(steps),
                "failed_count": sum(step.status in {"failed", "denied"} for step in steps),
            },
            "completeness": "complete" if self.ended is not None else "incomplete",
            "truncation": {"summaries_dropped": 0, "budget_bytes": MAX_DIAGNOSTICS_BYTES},
            "steps": [],
        }
        for step in steps:
            result["steps"].append({
                "call_id": _bounded_text(step.call_id), "tool_name": _bounded_text(step.tool_name), "status": _bounded_text(step.status, 32),
                "start_offset_ms": round((step.started - self.started) * 1000, 3) if step.started is not None else None,
                "duration_ms": round((step.ended - step.started) * 1000, 3) if step.started is not None and step.ended is not None else None,
                "input": None, "output": None, "error": None,
            })
        # IDs/timing/status get first claim on the budget. Then distribute each
        # summary fairly rather than letting one huge early call consume it.
        remaining = MAX_DIAGNOSTICS_BYTES - _bytes(result)
        per_field_budget = max(128, min(MAX_SUMMARY_BYTES, remaining // max(1, len(steps) * 2)))
        for index, step in enumerate(steps):
            for field_name in ("input", "output", "error"):
                value = getattr(step, field_name)
                if value is None:
                    continue
                if _bytes(value) <= per_field_budget:
                    result["steps"][index][field_name] = value
                else:
                    result["steps"][index][field_name] = {"state": "truncated", "value": None}
                    result["truncation"]["summaries_dropped"] += 1
        # Last guard covers JSON overhead and pathological identifiers.
        if _bytes(result) > MAX_DIAGNOSTICS_BYTES:
            for row in result["steps"]:
                for field_name in ("input", "output", "error"):
                    if row[field_name] is not None:
                        row[field_name] = {"state": "truncated", "value": None}
            result["truncation"]["summaries_dropped"] = sum(
                getattr(step, field_name) is not None for step in steps
                for field_name in ("input", "output", "error")
            )
        return result


current_collector: contextvars.ContextVar[Collector | None] = contextvars.ContextVar("ptc_diagnostics", default=None)
