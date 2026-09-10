"""Useful native tool failures, with operational facts kept separate from text."""
from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from urllib.parse import urlsplit, urlunsplit

from deep_agent.agent.observability import log_event

_SECRET_KEY = re.compile(r"authorization|cookie|token|secret|password|api[-_]?key", re.I)
_ASSIGNMENT = re.compile(
    r'''(?ix)(["']?(?:authorization|cookie|set-cookie|apptoken|access_token|refresh_token|token|password|secret|api[_-]?key)["']?\s*[:=]\s*)(?:"[^"\n]*"|'[^'\n]*'|[^\s,;}]+)'''
)
_URL = re.compile(r'''https?://[^\s<>"']+''')
_PATH = re.compile(r'''(?<![\w:])(?:/(?:private/)?(?:Users|home|tmp|var|deep-agent)/[^\s"';,]+|[A-Za-z]:\\[^\s"';,]+)''')
_BASE64 = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{200,}={0,2}")
_MAX_TEXT = 1600


def _chain(exc: BaseException) -> list[BaseException]:
    result = []
    seen = set()
    while exc is not None and id(exc) not in seen and len(result) < 8:
        seen.add(id(exc))
        result.append(exc)
        # Implicit context may be a different failed operation (e.g. upload
        # followed by failed cleanup). Only explicit causes establish lineage.
        exc = exc.__cause__
    return result


def _error_payload(value: object) -> dict:
    """Select error fields only; never echo a response's data/body/headers."""
    if type(value) is not dict:
        return {}
    nested = value.get("error")
    payload = dict(value)
    if type(nested) is dict:
        payload.update(nested)
    return {
        key: payload[key] for key in ("message", "msg", "detail", "error_description", "code", "type")
        if type(payload.get(key)) in (str, int)
    }


def native_error_details(exc: BaseException) -> dict:
    """No network or mutation. Walk wrappers to retain the original cause."""
    chain = _chain(exc)
    result = {"error_type": type(chain[-1]).__name__}
    structured_response = False
    for item in reversed(chain):
        body = getattr(item, "body", None)
        payload = _error_payload(body)
        detail = getattr(item, "detail", None)
        payload.update(_error_payload(detail))
        response = getattr(item, "response", None)
        structured_response |= body is not None or response is not None or type(detail) is dict
        unsafe_model_error = type(item).__name__ == "APIResponseModelError"
        structured_response |= unsafe_model_error
        if response is not None:
            try:
                payload.update(_error_payload(response.json()))
            except Exception:
                pass
        message = getattr(item, "upstream_message", None)
        if not message:
            message = payload.get("message") or payload.get("msg") or payload.get("detail") or payload.get("error_description")
        if not message and type(detail) is str and not unsafe_model_error:
            message = detail
        code = getattr(item, "upstream_code", None)
        if code is None:
            code = payload.get("code", getattr(item, "code", None))
        if code is None and isinstance(item, OSError):
            code = item.errno
        protocol_error = getattr(item, "error", None)
        if code is None:
            code = getattr(protocol_error, "code", None)
        if not message:
            message = getattr(protocol_error, "message", None)
        native_type = getattr(item, "upstream_error_type", None) or payload.get("type")
        status = getattr(item, "upstream_http_status", None)
        if status is None:
            status = getattr(response, "status_code", None)
        if status is None:
            # Platform ExpectedError uses HTTP 200 for its *local* envelope.
            candidate = getattr(item, "status_code", None)
            status = candidate if type(candidate) is int and candidate >= 400 else None
        for key, value in (("message", message), ("error_code", code),
                           ("native_error_type", native_type), ("http_status", status)):
            if key not in result and type(value) in (str, int) and value != "":
                result[key] = value
    if "message" not in result and not structured_response:
        # Prefer the deepest nonempty cause; TimeoutError often has no text.
        for item in reversed(chain):
            try:
                # OSError.__str__ includes absolute filename/filename2, which
                # are server implementation details, not an upstream reason.
                message = item.strerror if isinstance(item, OSError) and item.strerror else str(item)
            except Exception:
                continue
            if message:
                result["message"] = message
                break
    return result


def native_error_facts(exc: BaseException, request_headers: Mapping[str, str] | None = None) -> dict:
    """Operational scalars only; even a nominal business code may echo a key."""
    details = native_error_details(exc)
    result = {"error_type": details["error_type"], "http_status": details.get("http_status")}
    code = details.get("error_code")
    if type(code) in (str, int):
        value = str(code)
        if _safe_text(value, request_headers) == value:
            result["error_code"] = value
    return result


def _safe_text(value: object, request_headers: Mapping[str, str] | None = None) -> str:
    text = str(value)
    for key, secret in (request_headers or {}).items():
        if _SECRET_KEY.search(key) and secret:
            text = text.replace(secret, "[redacted]")
            if secret.lower().startswith("bearer "):
                text = text.replace(secret[7:], "[redacted]")
    text = re.sub(r"(?i)Bearer\s+[A-Za-z0-9._~+/=-]+", "Bearer [redacted]", text)
    text = _ASSIGNMENT.sub(r"\1[redacted]", text)

    def safe_url(match: re.Match) -> str:
        try:
            url = urlsplit(match.group())
            return urlunsplit((url.scheme, url.netloc.rsplit("@", 1)[-1], url.path, "", ""))
        except ValueError:
            return "[url]"

    text = _URL.sub(safe_url, text)
    text = _PATH.sub("[local-path]", text)
    text = _BASE64.sub("[binary-data]", text)
    text = " ".join(text.split())
    return text[:_MAX_TEXT] + ("…（原生错误过长，已截断）" if len(text) > _MAX_TEXT else "")


def tool_error_message(summary: str, exc: BaseException,
                       request_headers: Mapping[str, str] | None = None) -> str:
    """Keep the wrapper and actionable native reason in the model's Tool Result."""
    try:
        details = native_error_details(exc)
        facts = [f"类型={details['error_type']}"]
        for key, label in (("http_status", "HTTP"), ("error_code", "错误码"),
                           ("native_error_type", "原生类型"), ("message", "提示")):
            if key in details:
                facts.append(f"{label}={_safe_text(details[key], request_headers)}")
        return summary + "；原生错误：" + "；".join(facts)
    except Exception:
        return summary + "；原生错误类型=" + type(exc).__name__


def _emit(ctx, phase: str, status: str, started: float, *, tool: str | None,
          exc: BaseException | None = None, byte_size: int | None = None) -> None:
    try:
        deps = ctx.deps
        fields = dict(
            session_id=deps.session_id, run_id=deps.run_id or ctx.run_id,
            conversation_id=deps.conversation_id or ctx.conversation_id, agent_id=deps.agent_id,
            tool=tool or ctx.tool_name, tool_call_id=ctx.tool_call_id,
            phase=phase, status=status, duration_ms=max(0, round((time.monotonic()-started)*1000)),
            byte_size=byte_size,
        )
        if exc is not None:
            # Native text belongs in the authorized conversation, never logs.
            fields.update(native_error_facts(exc, deps.request_headers))
        log_event("builtin_operation_" + status, **fields)
    except Exception:
        pass


@asynccontextmanager
async def observe_tool_operation(ctx, phase: str, *, tool: str | None = None,
                                 byte_size: int | None = None):
    """Observe an existing I/O step without retrying or changing its outcome."""
    started = time.monotonic()
    _emit(ctx, phase, "started", started, tool=tool, byte_size=byte_size)
    try:
        yield
    except asyncio.CancelledError:
        _emit(ctx, phase, "cancelled", started, tool=tool, byte_size=byte_size)
        raise
    except Exception as exc:
        _emit(ctx, phase, "failed", started, tool=tool, exc=exc, byte_size=byte_size)
        raise
    else:
        _emit(ctx, phase, "completed", started, tool=tool, byte_size=byte_size)
