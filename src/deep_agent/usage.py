"""One durable record per observed model response, using SDK RequestUsage."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from deep_agent.agent.cache_usage import cache_message_summary


def _get(value: Any, key: str, default=None):
    return value.get(key, default) if isinstance(value, Mapping) else getattr(value, key, default)


def response_call(response: Any, *, request_id: str, model_id: str | None,
                  provider_id: str | None, purpose: str) -> dict[str, Any]:
    """Use the response's own usage; never take a shared RunUsage delta."""
    usage = _get(response, "usage")
    cache = cache_message_summary(response)
    def tokens(name):
        value = _get(usage, name)
        # RequestUsage keeps explicit provider fields in __dict__. Its normal
        # attribute access and serialized form supply default zeroes, which
        # cannot prove that a provider actually reported a counter.
        if isinstance(usage, Mapping):
            reported = name in usage and value != 0
        else:
            reported = name in getattr(usage, "__dict__", {})
        return value if reported and type(value) is int and value >= 0 else None
    input_tokens, output_tokens = tokens("input_tokens"), tokens("output_tokens")
    state = "reported" if input_tokens is not None and output_tokens is not None else (
        "partial" if input_tokens is not None or output_tokens is not None else "unreported")
    return {
        "request_id": request_id,
        "kind": "model", "purpose": purpose,
        "model_id": model_id,
        "model_name": _get(response, "model_name"),
        "provider_id": provider_id,
        "provider_name": _get(response, "provider_name"),
        "provider_response_id": _get(response, "provider_response_id"),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_read_tokens": cache["cache_read_tokens"],
        "cache_write_tokens": cache["cache_write_tokens"],
        "state": state,
    }


def merge_calls(document: dict | None, calls: list[dict]) -> dict | None:
    if not calls:
        return document
    previous = list((document or {}).get("calls") or [])
    by_id = {call["request_id"]: index for index, call in enumerate(previous)}
    for call in calls:
        index = by_id.get(call["request_id"])
        if index is None:
            by_id[call["request_id"]] = len(previous)
            previous.append(call)
        elif ("unreported", "partial", "reported").index(call.get("state", "unreported")) >= (
            "unreported", "partial", "reported").index(previous[index].get("state", "unreported")):
            previous[index] = call
    return {"version": 1, "calls": previous}
