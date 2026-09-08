"""Bounded diagnostic facts, not raw exceptions or request payloads."""
from __future__ import annotations

import re
from typing import Any


def safe_execution_error(payload: dict[str, Any]) -> dict[str, str]:
    source = payload
    code = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(source.get("code") or "execution_failed"))[:96]
    phase = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(source.get("phase") or payload.get("phase") or "execution"))[:64]
    message = str(source.get("message") or "本轮执行异常终止。")
    message = message.splitlines()[0] if message else "本轮执行异常终止。"
    message = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", message)
    message = re.sub(r"https?://\S+", "[URL omitted]", message)
    message = re.sub(r"(?i)(?:[\"']?)(?:api[_-]?key|access[_-]?token|authorization|cookie|password|secret|token)[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)", "[credential omitted]", message)
    message = re.sub(r"\b[A-Za-z0-9_+/=-]{24,}\b", "[opaque value omitted]", message)
    return {"code": code, "message": message[:512], "phase": phase}
