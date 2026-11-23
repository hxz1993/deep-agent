from __future__ import annotations

import time
from collections.abc import Mapping
from contextlib import contextmanager
from typing import Any, Iterable

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai.usage import RunUsage


def token_usage_available(usage: Any) -> bool:
    """SDK default zero counters do not establish provider token measurement.

    Preserve raw usage in message events; only presentation/aggregation treats
    an entirely empty token measurement as unknown. A reported zero output is
    still valid when the response has measured input (and vice versa).
    """
    return any(
        isinstance(value, int) and not isinstance(value, bool) and value > 0
        for name in ("input_tokens", "output_tokens")
        for value in [usage.get(name) if isinstance(usage, Mapping) else getattr(usage, name, None)]
    )


def usage_dict(usage: RunUsage | None) -> dict[str, int | None]:
    if usage is None:
        return {
            "requests": None,
            "tool_calls": None,
            "input_tokens": None,
            "output_tokens": None,
            "total_tokens": None,
            "cache_read_tokens": None,
            "cache_write_tokens": None,
        }
    measured = token_usage_available(usage)
    return {
        "requests": usage.requests,
        "tool_calls": usage.tool_calls,
        "input_tokens": usage.input_tokens if measured else None,
        "output_tokens": usage.output_tokens if measured else None,
        "total_tokens": usage.total_tokens if measured else None,
        "cache_read_tokens": usage.cache_read_tokens if measured else None,
        "cache_write_tokens": usage.cache_write_tokens if measured else None,
    }


def cache_usage_available(messages: Iterable[ModelMessage]) -> bool:
    """Return whether a provider response actually supplied cache counters.

    Pydantic AI normalizes absent counters to zero. Individual request usage
    objects still preserve which fields were present, which lets telemetry keep
    unsupported counters as unknown instead of inventing a cache miss.
    """

    for message in messages:
        if not isinstance(message, ModelResponse):
            continue
        usage = message.usage
        if {"cache_read_tokens", "cache_write_tokens"} & usage.__dict__.keys():
            return True
        if any("cache" in key.lower() for key in usage.details):
            return True
    return False


def run_metrics(
    *,
    status: str,
    started_at: float,
    model_id: str | None,
    usage: RunUsage | None,
    tool_search_calls: int = 0,
    compaction: str = "none",
    cache_metrics_available: bool | None = None,
    messages: Iterable[ModelMessage] | None = None,
) -> dict[str, Any]:
    metrics = {
        "model_id": model_id,
        "status": status,
        "duration_ms": max(0, round((time.perf_counter() - started_at) * 1_000)),
        **usage_dict(usage),
        "tool_search_calls": tool_search_calls,
        "compaction": compaction,
    }
    if cache_metrics_available is False or (
        cache_metrics_available is None
        and not (metrics["cache_read_tokens"] or metrics["cache_write_tokens"])
    ):
        metrics["cache_read_tokens"] = None
        metrics["cache_write_tokens"] = None
    if messages is not None:
        from deep_agent.agent.cache_usage import summarize_cache_usage

        cache = summarize_cache_usage(messages)
        metrics["cache_usage"] = cache
        metrics["cache_read_tokens"] = cache["cache_read_tokens"]
        metrics["cache_write_tokens"] = cache["cache_write_tokens"]
    return metrics


def log_event(event: str, *, level: str | None = None, exception: BaseException | None = None, **fields: Any) -> None:
    """The unified, allowlisted diagnostic writer is never a business dependency."""
    try:
        from deep_agent.diagnostics import emit

        if event == "agent_run_completed" and fields.get("status") == "pending":
            event = "agent_run_pending"
        if level is None:
            level = "ERROR" if event.endswith("_failed") else "WARNING" if event.endswith("_retrying") else "INFO"
        if exception is not None:
            fields["exception"] = exception
        emit(event, level=level, **fields)
    except Exception:
        pass


def log_run_phase(phase: str, started_at: float, *, ended_at: float | None = None, **fields: Any) -> None:
    end = time.perf_counter() if ended_at is None else ended_at
    log_event("agent_run_phase", phase=phase, duration_ms=max(0, round((end - started_at) * 1000)), **fields)


@contextmanager
def observe_run_phase(phase: str, **fields: Any):
    started_at = time.perf_counter()
    try:
        yield
    finally:
        log_run_phase(phase, started_at, **fields)
