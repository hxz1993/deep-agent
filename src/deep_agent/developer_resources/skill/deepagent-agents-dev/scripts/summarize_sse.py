#!/usr/bin/env python3
"""Summarize DeepAgent SSE without printing prompt or tool arguments by default."""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path
from typing import Any, Iterable, TextIO


SENSITIVE_KEYS = {"message", "content", "prompt", "token", "cookie", "authorization"}


def read_events(stream: TextIO) -> tuple[list[tuple[str, str]], list[str]]:
    events: list[tuple[str, str]] = []
    malformed: list[str] = []
    event_name = "message"
    data_lines: list[str] = []

    def flush() -> None:
        nonlocal event_name, data_lines
        if data_lines:
            events.append((event_name, "\n".join(data_lines)))
        event_name = "message"
        data_lines = []

    for line_number, raw_line in enumerate(stream, 1):
        line = raw_line.rstrip("\r\n")
        if not line:
            flush()
        elif line.startswith(":"):
            continue
        elif line.startswith("event:"):
            event_name = line[6:].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
        else:
            # Malformed text may itself be a prompt, credential or tool value.
            malformed.append(f"line {line_number}: {len(line)} characters (content omitted)")
    flush()
    return events, malformed


def walk(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for nested in value.values():
            yield from walk(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from walk(nested)


def safe_shape(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: "<redacted>" if key.lower() in SENSITIVE_KEYS else safe_shape(item) for key, item in value.items()}
    if isinstance(value, list):
        return [safe_shape(item) for item in value[:20]] + (["<truncated>"] if len(value) > 20 else [])
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return f"<{type(value).__name__}>"


def event_summary(index: int, name: str, raw_data: str, show_args: bool) -> dict[str, Any]:
    summary: dict[str, Any] = {"index": index, "event": name}
    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError as exc:
        summary["json_error"] = f"line {exc.lineno}, column {exc.colno}"
        summary["data_length"] = len(raw_data)
        return summary

    if isinstance(payload, dict):
        summary["keys"] = sorted(payload.keys())
        for key in ("session_id", "run_id", "turn_id", "output_id", "name", "attempt", "accepted", "status", "kind", "type", "tool_name", "tool_call_id"):
            value = payload.get(key)
            if isinstance(value, (str, int, float, bool)):
                summary[key] = value

    if isinstance(payload, dict) and isinstance(payload.get("output_info"), dict):
        summary["output_info"] = {key: payload["output_info"][key] for key in
            ("name", "output_id", "tool_call_id", "attempt", "turn_id") if key in payload["output_info"]}
    if isinstance(payload, dict) and "output" in payload:
        value = payload["output"]
        summary["output_type"] = "null" if value is None else type(value).__name__

    calls: list[dict[str, Any]] = []
    # Business output/tool results are not new dispatched tool calls.
    for item in walk(payload) if name == "tool_call" else ():
        tool_name = item.get("tool_name") or item.get("tool") or item.get("name")
        call_id = item.get("tool_call_id") or item.get("call_id") or item.get("id")
        args = item.get("args", item.get("arguments"))
        looks_like_call = args is not None and (tool_name is not None or call_id is not None)
        if not looks_like_call:
            continue
        call: dict[str, Any] = {
            "tool": tool_name,
            "tool_call_id": call_id,
            "args_type": type(args).__name__,
        }
        if isinstance(args, dict):
            call["arg_keys"] = sorted(args.keys())
        if show_args:
            call["args"] = safe_shape(args)
        calls.append(call)
    if calls:
        summary["tool_calls"] = calls
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("path", help="SSE file path or '-' for stdin.")
    parser.add_argument("--show-args", action="store_true", help="Show redacted argument shapes, never raw strings.")
    args = parser.parse_args()

    stream: TextIO
    should_close = False
    if args.path == "-":
        stream = sys.stdin
    else:
        stream = Path(args.path).open("r", encoding="utf-8")
        should_close = True
    try:
        events, malformed = read_events(stream)
    finally:
        if should_close:
            stream.close()

    counts = collections.Counter(name for name, _ in events)
    result = {
        "event_count": len(events),
        "event_types": dict(counts),
        "malformed_line_count": len(malformed),
        "events": [event_summary(index, name, data, args.show_args) for index, (name, data) in enumerate(events, 1)],
    }
    if malformed:
        result["malformed_lines"] = malformed[:20]
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if malformed else 0


if __name__ == "__main__":
    raise SystemExit(main())
