"""Bounded diagnostic facts, not raw exceptions or request payloads."""
from __future__ import annotations

import re
from typing import Any


def safe_execution_error(payload: dict[str, Any]) -> dict[str, Any]:
    source = payload
    code = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(source.get("code") or "execution_failed"))[:96]
    phase = re.sub(r"[^a-zA-Z0-9_.-]", "_", str(source.get("phase") or payload.get("phase") or "execution"))[:64]
    message = str(source.get("message") or "本轮执行异常终止。")
    message = message.splitlines()[0] if message else "本轮执行异常终止。"
    message = re.sub(r"(?i)\bBearer\s+\S+", "Bearer [REDACTED]", message)
    message = re.sub(r"https?://\S+", "[URL omitted]", message)
    message = re.sub(r"(?i)(?:[\"']?)(?:api[_-]?key|access[_-]?token|authorization|cookie|password|secret|token)[\"']?\s*[:=]\s*(?:\"[^\"]*\"|'[^']*'|[^\s,;}]+)", "[credential omitted]", message)
    message = re.sub(r"\b[A-Za-z0-9_+/=-]{24,}\b", "[opaque value omitted]", message)
    result = {"code": code, "message": message[:512], "phase": phase}
    budget = source.get("deepagent_output_budget")
    if isinstance(budget, dict):
        keys = budget.get("rejected_response_keys")
        if (budget.get("correction_limit") == 2 and isinstance(keys, list) and len(keys) <= 3
                and all(isinstance(key, str) and re.fullmatch(r"[a-f0-9-]{1,128}:[0-9]{1,8}", key) for key in keys)):
            result["deepagent_output_budget"] = {"correction_limit": 2, "rejected_response_keys": keys,
                "rejected_responses": len(keys), "remaining_corrections": max(0, 2 - len(keys))}
    return result
