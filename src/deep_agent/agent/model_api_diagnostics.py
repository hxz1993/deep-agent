"""Observe serialized model HTTP requests, never their prompts or credentials.

The OpenAI SDK transport seam is isolated here and covered with the pinned SDK.
Hooks use HTTPX's public event_hooks API; client ownership, retries and streaming
remain entirely with the provider. No response body is consumed by observation.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
import hashlib
import json
import time
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from httpx2 import AsyncByteStream
from pydantic_ai.models.wrapper import WrapperModel

from deep_agent.agent.observability import log_event

MAX_INSPECT_BYTES = 32 * 1024 * 1024


@dataclass
class _Scope:
    fields: dict[str, Any]
    previous: dict[str, set[str]]
    pending: dict[int, tuple[dict, float]] = field(default_factory=dict)


_scope: ContextVar[_Scope | None] = ContextVar("model_api_diagnostics", default=None)


def _emit(event: str, **fields: Any) -> None:
    try:
        log_event(event, **fields)
    except Exception:
        pass


def _endpoint(url: str) -> dict:
    parts = urlsplit(url)
    # Origin only: paths in custom proxies can contain credentials as well.
    host = parts.hostname or ""
    if ":" in host:
        host = f"[{host}]"
    origin = f"{parts.scheme}://{host}" + (f":{parts.port}" if parts.port else "")
    known = ("/chat/completions", "/responses", "/messages")
    path = next((suffix for suffix in known if parts.path.rstrip("/").endswith(suffix)), "/custom")
    return {"model_api": origin, "api_path": path}


def _tool_names(tools: list) -> list[str]:
    names = []
    for tool in tools:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function")
        name = function.get("name") if isinstance(function, dict) else tool.get("name")
        # Responses native tools have a type rather than a function name.
        # Record only known protocol identifiers, never arbitrary tool config.
        if name is None and tool.get("type") in {
            "web_search", "web_search_preview", "tool_search", "file_search",
            "code_interpreter", "image_generation", "computer_use_preview",
        }:
            name = tool["type"]
        if type(name) is str:
            names.append(name)
    return names


def _manifest(request: Any, previous: dict[str, set[str]], key: str) -> dict:
    # Only inspect an already-buffered request. Never read/consume a body stream.
    try:
        body = request.content
    except Exception:
        return {"tool_manifest_status": "unavailable"}
    if len(body) > MAX_INSPECT_BYTES:
        return {"tool_manifest_status": "body_too_large"}
    payload = json.loads(body)
    tools = payload.get("tools", [])
    if not isinstance(tools, list):
        return {"tool_manifest_status": "unsupported"}
    names = _tool_names(tools)
    # Responses can introduce definitions in search-result history as well as
    # top-level tools. Inspect only these schema names, never message content.
    history_names = []
    inputs = payload.get("input", [])
    if isinstance(inputs, list):
        for item in inputs:
            if (isinstance(item, dict) and item.get("type") == "tool_search_output"
                    and isinstance(item.get("tools"), list)):
                history_names.extend(_tool_names(item["tools"]))
    counts = Counter(names)
    current = set(names)
    added = sorted(current - previous.get(key, set()))
    if key in previous or len(previous) < 12:
        previous[key] = current
    return {
        "tool_manifest_status": "complete", "tool_count": len(names),
        "unique_tool_count": len(current), "tool_names": names,
        "duplicate_tool_names": sorted(name for name, count in counts.items() if count > 1),
        "added_tool_names": added,
        "history_tool_names": history_names,
        "redeclared_tool_names": sorted(current.intersection(history_names)),
        "tool_names_hash": hashlib.sha256(json.dumps(names, ensure_ascii=True).encode()).hexdigest(),
    }


async def _request_hook(request: Any) -> None:
    scope = _scope.get()
    if scope is None:
        return
    try:
        fields = {**scope.fields, **_endpoint(str(request.url)),
                  "http_method": request.method, "api_request_id": uuid4().hex}
        key = fields["model_api"] + fields["api_path"] + str(fields.get("model_name", ""))
        try:
            manifest = _manifest(request, scope.previous, key)
        except Exception:
            manifest = {"tool_manifest_status": "unavailable"}
        if len(scope.pending) < 32:
            scope.pending[id(request)] = (fields, time.monotonic())
        _emit("model_api_request", **fields, **manifest)
    except Exception:
        pass


async def _response_hook(response: Any) -> None:
    scope = _scope.get()
    if scope is None:
        return
    try:
        pending = scope.pending.pop(id(response.request), None)
        if pending is None:
            return
        fields, started = pending
        _emit("model_api_response", **fields, http_status=response.status_code,
              duration_ms=max(0, round((time.monotonic() - started) * 1000)))
        if not response.is_stream_consumed and isinstance(response.stream, AsyncByteStream):
            response.stream = _ObservedByteStream(response.stream, {**fields, "http_status": response.status_code}, started)
    except Exception:
        pass


STREAM_FIRST_REPORT_SECONDS = 30
STREAM_REPORT_SECONDS = 60


class _ObservedByteStream(AsyncByteStream):
    """Count transport bytes when consumed, without buffering or background reads.

    Timer callbacks only snapshot counters. Their cadence is independent of
    incoming chunks, so heartbeats cannot indefinitely suppress diagnostics.
    Closing and iteration stay in the SDK's task and propagate unchanged.
    """

    def __init__(self, stream, fields, started):
        self._stream = stream
        self._fields = fields
        self._started = started
        self._chunks = self._bytes = 0
        self._first = self._last = None
        self._state = "unconsumed"
        self._timer = None
        self._schedule(STREAM_FIRST_REPORT_SECONDS)

    def _schedule(self, delay):
        try:
            self._timer = asyncio.get_running_loop().call_later(delay, self._report)
        except Exception:
            pass

    def _snapshot(self, event):
        try:
            now = time.monotonic()
            fields = dict(self._fields, stream_state=self._state,
                          stream_chunks=self._chunks, stream_bytes=self._bytes,
                          duration_ms=max(0, round((now - self._started) * 1000)))
            if self._first is not None:
                fields["first_byte_ms"] = max(0, round((self._first - self._started) * 1000))
                fields["last_byte_age_ms"] = max(0, round((now - self._last) * 1000))
            _emit(event, **fields)
        except Exception:
            pass

    def _report(self):
        self._timer = None
        self._snapshot("model_api_stream_progress")
        self._schedule(STREAM_REPORT_SECONDS)

    def _finish(self, state):
        try:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            if self._state not in {"eof", "cancelled", "failed", "closed", "closed_unconsumed", "close_failed"}:
                self._state = state
                self._snapshot("model_api_stream_finished")
        except Exception:
            pass

    def _record_chunk(self, chunk):
        try:
            self._chunks += 1
            self._bytes += len(chunk)
            if chunk:
                self._last = time.monotonic()
                if self._first is None:
                    self._first = self._last
                    self._snapshot("model_api_stream_first_byte")
        except Exception:
            pass

    async def __aiter__(self):
        try:
            iterator = self._stream.__aiter__()
            while True:
                self._state = "reading"
                try:
                    chunk = await anext(iterator)
                except StopAsyncIteration:
                    self._finish("eof")
                    return
                self._record_chunk(chunk)
                self._state = "consumer_idle"
                yield chunk
        except asyncio.CancelledError:
            self._finish("cancelled")
            raise
        except GeneratorExit:
            self._finish("closed")
            raise
        except BaseException:
            self._finish("failed")
            raise

    async def aclose(self):
        try:
            await self._stream.aclose()
        except asyncio.CancelledError:
            self._finish("cancelled")
            raise
        except BaseException:
            self._finish("close_failed")
            raise
        else:
            self._finish("closed_unconsumed" if self._state == "unconsumed" else "closed")


@contextmanager
def observe_model_api(model: Any, fields: dict, previous: dict[str, set[str]]):
    """Bind once per actual client; scope is isolated between concurrent Runs."""
    token = None
    try:
        from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel

        if isinstance(model, (OpenAIChatModel, OpenAIResponsesModel)):
            # Pinned OpenAI 3.5 owns this HTTP client. Do not replace it or attach
            # new clients (which would change pooling, proxy or close semantics).
            hooks = model.client._client.event_hooks
            for kind, hook in (("request", _request_hook), ("response", _response_hook)):
                if hook not in hooks[kind]:
                    hooks[kind].append(hook)
            token = _scope.set(_Scope(dict(fields), previous))
    except Exception:
        pass
    try:
        yield
    finally:
        if token is not None:
            _scope.reset(token)


class _ObservedModel(WrapperModel):
    """Enter the scope where lazy streaming actually opens the HTTP request.

    The SDK consumes streams in a different task from capability middleware.
    Delegate every argument and response unchanged; never consume the stream.
    """

    def __init__(self, wrapped, fields, previous):
        super().__init__(wrapped)
        self._fields = fields
        self._previous = previous

    async def request(self, messages, model_settings, model_request_parameters):
        with observe_model_api(self.wrapped, self._fields, self._previous):
            return await self.wrapped.request(messages, model_settings, model_request_parameters)

    @asynccontextmanager
    async def request_stream(self, messages, model_settings, model_request_parameters, run_context=None):
        with observe_model_api(self.wrapped, self._fields, self._previous):
            async with self.wrapped.request_stream(
                messages, model_settings, model_request_parameters, run_context
            ) as stream:
                yield stream


def observed_model(model, fields, previous):
    """Only adapt supported transports; unknown/custom models remain untouched."""
    try:
        from pydantic_ai.models.openai import OpenAIChatModel, OpenAIResponsesModel
        if isinstance(model, (OpenAIChatModel, OpenAIResponsesModel)):
            return _ObservedModel(model, fields, previous)
    except Exception:
        pass
    return model
