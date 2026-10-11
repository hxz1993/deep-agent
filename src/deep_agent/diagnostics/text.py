"""Human-readable pipe logs. Escaping keeps each emitted record on one line."""
from __future__ import annotations

import re

from .schema import MAX_RECORD_BYTES, NUMBER_FIELDS, ADAPTATION_NUMBER_FIELDS, LAYOUT_NUMBER_FIELDS, BOOLEAN_FIELDS, TOOL_LIST_FIELDS, sanitize_record


def escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("|", "\\|").replace("\n", "\\n").replace("\r", "\\r")


def unescape(value: str) -> str:
    return re.sub(r"\\([\\|nr])", lambda m: {"n": "\n", "r": "\r"}.get(m[1], m[1]), value)


def _parts(line: str) -> list[str]:
    parts, start, escaped = [], 0, False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "|":
            parts.append(line[start:index].strip())
            start = index + 1
    parts.append(line[start:].strip())
    return parts


def format_record(payload: dict) -> str:
    """Only accept the same safe facts as the existing logging call sites."""
    record = sanitize_record(payload)
    if record is None:
        raise ValueError("Invalid operational record")
    line = _render(record)
    # Account for the actual UTF-8 wire representation, including escaping and
    # the writer's newline. Keep scalar facts; trim the existing bounded tool
    # inventories first, in reverse priority order, with an explicit count.
    for key in reversed(TOOL_LIST_FIELDS):
        while len(line.encode()) + 1 > MAX_RECORD_BYTES and record.get(key):
            record[key].pop()
            record["tools_omitted"] = record.get("tools_omitted", 0) + 1
            line = _render(record)
    if len(line.encode()) + 1 > MAX_RECORD_BYTES:
        # Only the repeated prose is shortened. The complete scalar facts stay
        # in the appended fields; never split a UTF-8 character or escape pair.
        message = record["summary"]
        low, high = 0, len(message)
        while low < high:
            middle = (low + high + 1) // 2
            record["summary"] = message[:middle] + "…"
            if len(_render(record).encode()) + 1 <= MAX_RECORD_BYTES:
                low = middle
            else:
                high = middle - 1
        record["summary"] = message[:low] + "…"
        line = _render(record)
    return line


def _render(record: dict) -> str:
    prefix = [record["timestamp"], str(record["process_id"]), record["level"],
              record.get("request_id", "-"), f'{record["source"]} - {record["summary"]}']
    excluded = {"schema_version", "timestamp", "process_id", "level", "source", "summary", "request_id"}
    # Business facts precede bookkeeping so even a narrow terminal is useful.
    envelope = {"event", "record_id", "boot_id", "instance_id"}
    keys = sorted(record.keys() - excluded - envelope) + sorted(envelope)
    for key in keys:
        value = record[key]
        text = "[" + ", ".join(value) + "]" if isinstance(value, list) else str(value)
        prefix.append(f"{key}={text}")
    return " | ".join(escape(part) for part in prefix)


def parse_record(line: str) -> dict | None:
    """Parse this text format only; JSON and unknown lines remain raw upstream."""
    try:
        parts = _parts(line)
        if len(parts) < 5:
            return None
        timestamp, pid, level, request_id, body = map(unescape, parts[:5])
        source, summary = body.split(" - ", 1)
        record = {"timestamp": timestamp, "process_id": int(pid), "level": level,
                  "source": source, "summary": summary}
        if request_id != "-":
            record["request_id"] = request_id
        reserved = set(record) | {"schema_version", "raw", "message"}
        for part in parts[5:]:
            key, value = unescape(part).split("=", 1)
            if key in reserved or key in record:
                return None
            if key in NUMBER_FIELDS or key in ADAPTATION_NUMBER_FIELDS or key in LAYOUT_NUMBER_FIELDS or key in ("http_status", "tools_omitted"):
                value = float(value) if "." in value or "e" in value.lower() else int(value)
            elif key in BOOLEAN_FIELDS:
                if value not in ("True", "False"):
                    return None
                value = value == "True"
            elif key in TOOL_LIST_FIELDS:
                if not value.startswith("[") or not value.endswith("]"):
                    return None
                value = value[1:-1].split(", ") if value != "[]" else []
            record[key] = value
        safe = sanitize_record(record)
        if safe is None:
            return None
        # The written body is authoritative; never regenerate it while reading.
        safe["summary"] = summary
        safe["message"] = summary
        safe["raw"] = line
        return safe
    except (ValueError, TypeError, OverflowError):
        return None
