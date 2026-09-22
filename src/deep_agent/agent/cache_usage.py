"""Conservative cache measurements; never modify the provider's original Usage.

Only the fresh response boundary may use the SDK's sparse usage fields as proof
of a reported zero. Serialization fills defaults, so historical zero needs the
provenance stamp or a recognized provider detail.
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

CACHE_USAGE_METADATA = "deepagent_cache_usage"


def _get(value: Any, key: str, default=None):
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def _count(value: Any) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _detail_count(message: Any, field: str) -> int | None:
    provider = _get(message, "provider_name")
    aliases = {
        "deepseek": {"read": "prompt_cache_hit_tokens"},
        "anthropic": {"read": "cache_read_input_tokens", "write": "cache_creation_input_tokens"},
    }
    key = aliases.get(provider, {}).get(field)
    details = _get(_get(message, "usage"), "details")
    return _count(details.get(key)) if key and isinstance(details, Mapping) else None


def _cache_count(message: Any, field: str, *, fresh: bool = False) -> int | None:
    usage = _get(message, "usage")
    name = f"cache_{field}_tokens"
    raw = _get(usage, name)
    normalized = _count(raw)
    # Invalid provider numbers are not turned into zero or repaired by aliases.
    if raw is not None and normalized is None:
        return None
    metadata = _get(message, "metadata")
    stamp = metadata.get(CACHE_USAGE_METADATA) if isinstance(metadata, Mapping) else None
    flag = stamp.get(f"{field}_reported") if isinstance(stamp, Mapping) and stamp.get("version") == 1 else None
    if type(flag) is bool and not fresh:
        if not flag:
            return None
        detail = _detail_count(message, field)
        return normalized if normalized else detail if detail is not None else normalized
    if normalized is not None and normalized > 0:
        return normalized
    detail = _detail_count(message, field)
    if detail is not None:
        return detail
    if fresh and name in getattr(usage, "__dict__", {}):
        return normalized
    return None


def stamp_cache_usage(response: Any) -> None:
    """Stamp one freshly completed SDK response before it is persisted."""
    if _get(response, "kind") != "response":
        return
    response.metadata = {
        **(response.metadata or {}),
        CACHE_USAGE_METADATA: {
            "version": 1,
            "read_reported": _cache_count(response, "read", fresh=True) is not None,
            "write_reported": _cache_count(response, "write", fresh=True) is not None,
        },
    }


def cache_message_summary(message: Any) -> dict[str, Any]:
    """Single-response summary; non-response messages have an empty summary."""
    if _get(message, "kind") != "response":
        return _empty_summary()
    usage = _get(message, "usage")
    input_tokens = _count(_get(usage, "input_tokens"))
    if input_tokens == 0 and not (_count(_get(usage, "output_tokens")) or 0):
        input_tokens = None  # Serialized SDK defaults are not a measurement.
    read = _cache_count(message, "read")
    write = _cache_count(message, "write")
    ratio = read / input_tokens if input_tokens and read is not None and read <= input_tokens else None
    return {"input_tokens": input_tokens, "cache_read_tokens": read, "cache_write_tokens": write,
            "read_complete": read is not None, "write_complete": write is not None,
            "response_count": 1, "hit_ratio": ratio}


def _empty_summary() -> dict[str, Any]:
    return {"input_tokens": None, "cache_read_tokens": None, "cache_write_tokens": None,
            "read_complete": False, "write_complete": False, "response_count": 0, "hit_ratio": None}


def summarize_cache_usage(messages) -> dict[str, Any]:
    """Aggregate unique model responses (callers group split persisted events)."""
    rows = []
    seen = set()
    for message in messages:
        if _get(message, "kind") != "response":
            continue
        # Provider IDs alone may be reused across runs/providers; use the SDK's
        # run-local identity when present. With no stable identity, count each.
        run_id, response_id = _get(message, "run_id"), _get(message, "provider_response_id")
        identity = (run_id, _get(message, "provider_name"), _get(message, "model_name"), response_id)
        if run_id and response_id:
            if identity in seen:
                continue
            seen.add(identity)
        rows.append(cache_message_summary(message))
    if not rows:
        return _empty_summary()
    result = {"response_count": len(rows)}
    for field in ("input_tokens", "cache_read_tokens", "cache_write_tokens"):
        values = [row[field] for row in rows if row[field] is not None]
        result[field] = sum(values) if values else None
    result["read_complete"] = all(row["read_complete"] for row in rows)
    result["write_complete"] = all(row["write_complete"] for row in rows)
    complete_input = all(row["input_tokens"] is not None for row in rows)
    valid_ratios = all(row["hit_ratio"] is not None for row in rows)
    result["hit_ratio"] = (result["cache_read_tokens"] / result["input_tokens"]
        if complete_input and result["read_complete"] and valid_ratios and result["input_tokens"] else None)
    return result
