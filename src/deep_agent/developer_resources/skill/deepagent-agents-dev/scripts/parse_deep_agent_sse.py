#!/usr/bin/env python3
"""Summarize private DeepAgent SSE; omit business output and raw tool args by default."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any


def parse_frames(text: str) -> list[tuple[str, Any]]:
    events: list[tuple[str, Any]] = []
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    for frame in normalized.split("\n\n"):
        event, lines = "message", []
        for line in frame.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip() or "message"
            elif line.startswith("data:"):
                lines.append(line[5:].removeprefix(" "))
        if not lines:
            continue
        raw = "\n".join(lines)
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = raw
        events.append((event, data))
    return events


def preview(value: Any, max_chars: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = text.replace("\n", " ")
    return text if len(text) <= max_chars else text[:max_chars] + "..."


def analyze(events: list[tuple[str, Any]], *, show_args: bool = False) -> dict[str, Any]:
    facts: dict[str, dict[str, Any]] = {}
    conflicts, invalid, attempts = [], [], []
    # Public SSE can replay frames and reuse IDs across Runs/messages. Preserve
    # frames for diagnosis; only persisted call sources prove execution counts.
    calls: list[dict[str, Any]] = []
    final = None
    session = None
    run_id = None
    turn_id = None
    for index, (name, data) in enumerate(events, 1):
        if not isinstance(data, dict):
            invalid.append({"index": index, "event": name})
            continue
        if name == "session":
            session = data.get("session_id")
            run_id = data.get("run_id")
            turn_id = data.get("turn_id")
        if name == "tool_call":
            call = {"frame_index": index, "session_id": data.get("session_id", session),
                    "run_id": data.get("run_id", run_id), "turn_id": data.get("turn_id", turn_id),
                    "tool": data.get("tool"), "tool_call_id": data.get("tool_call_id")}
            if show_args:
                args = data.get("args")
                call["args_type"] = type(args).__name__
                if isinstance(args, dict):
                    call["arg_keys"] = sorted(args)
            calls.append(call)
        if name in ("final", "final.completed", "final.pending"):
            final = data
        info = None
        accepted = None
        if name == "output" and isinstance(data.get("accepted"), bool):
            info, accepted = data, data["accepted"]
            attempts.append({key: data[key] for key in ("output_id", "attempt", "accepted", "status", "run_id", "turn_id") if key in data})
        elif name in ("final", "final.completed") and data.get("status", "completed") == "completed" and isinstance(data.get("output_info"), dict):
            info, accepted = data["output_info"], True
        if info is None:
            continue
        output_id = info.get("output_id")
        if not isinstance(output_id, str) or not output_id:
            invalid.append({"index": index, "event": name, "reason": "missing output_id"})
            continue
        fact = {key: info.get(key) for key in ("name", "output_id", "tool_call_id", "attempt", "turn_id")}
        fact.update(accepted=accepted, status="completed" if accepted else data.get("status"))
        if accepted:
            if "output" not in data:
                invalid.append({"index": index, "event": name, "reason": "missing output"})
                continue
            fact["output"] = data["output"]
        else:
            fact["errors"] = data.get("errors")
        previous = facts.get(output_id)
        if previous is not None and json.dumps(previous, sort_keys=True) != json.dumps(fact, sort_keys=True):
            conflicts.append({"index": index, "output_id": output_id})
        else:
            facts[output_id] = fact
    result = {
        "session_id": session,
        "events": dict(Counter(name for name, _ in events)),
        "tool_calls": calls,
        "tool_call_frame_count": len(calls),
        "tool_call_scope": "raw frames; replay and reused IDs require persisted sources to count executions",
        "output_attempts": attempts,
        "unique_accepted_outputs": sum(fact["accepted"] is True for fact in facts.values()),
        "unique_rejected_outputs": sum(fact["accepted"] is False for fact in facts.values()),
        "conflicts": conflicts,
        "invalid_frames": invalid,
    }
    if final is not None:
        result["final_status"] = final.get("status")
        for key in ("usage", "run_metrics", "output_info"):
            if key in final:
                result[key] = final[key]
        if "output" in final:
            result["output_type"] = "null" if final["output"] is None else type(final["output"]).__name__
    return result


def summarize(path: Path, *, show_output: bool, max_chars: int, show_args: bool = False) -> dict[str, Any]:
    events = parse_frames(path.read_text(encoding="utf-8"))
    result = analyze(events, show_args=show_args)
    result["file"] = str(path)
    if show_output:
        final = next((d for n, d in reversed(events) if n in ("final", "final.completed") and isinstance(d, dict)), None)
        if final is not None and "output" in final:
            result["output_preview"] = preview(final["output"], max_chars)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("files", nargs="+", type=Path)
    parser.add_argument("--show-output", action="store_true", help="Explicitly print bounded business output; use private captures only.")
    parser.add_argument("--show-args", action="store_true", help="Show tool argument keys/types, never raw argument values.")
    parser.add_argument("--max-chars", type=int, default=2000)
    args = parser.parse_args()
    failed = False
    for path in args.files:
        result = summarize(path, show_output=args.show_output, max_chars=max(80, args.max_chars), show_args=args.show_args)
        failed |= bool(result["conflicts"] or result["invalid_frames"])
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
