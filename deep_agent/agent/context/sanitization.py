from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from pydantic_ai.messages import ModelMessage

from deep_agent.db.serialization import deserialize_message, serialize_message

_SENSITIVE_KEYS = {
    "authorization", "cookie", "headers", "api_key", "apikey", "password",
    "secret", "token", "access_token", "refresh_token", "x-user-token",
}
_MEDIA_KINDS = {"audio-url", "binary", "document-url", "image-url", "video-url"}
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|token|cookie)"
    r"(\s*[:=]\s*)([^\s,;\"']+)"
)
_SIGNED_URL_RE = re.compile(
    r"(?i)^https?://\S+[?&](?:token|signature|sig|x-amz-signature|x-goog-signature)="
)


def history_hash(messages: list[ModelMessage]) -> str:
    canonical = json.dumps(
        [serialize_message(message) for message in messages],
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


def _sanitize_string(value: str) -> str:
    if _SIGNED_URL_RE.search(value):
        return "[temporary URL omitted]"
    value = _BEARER_RE.sub("Bearer [REDACTED]", value)
    return _SECRET_ASSIGNMENT_RE.sub(r"\1\2[REDACTED]", value)


def _sanitize_value(value: Any, *, key: str | None = None) -> Any:
    if key is not None and key.lower() in _SENSITIVE_KEYS:
        return "[REDACTED]"
    if isinstance(value, str):
        return _sanitize_string(value)
    if isinstance(value, list):
        return [_sanitize_value(item) for item in value]
    if isinstance(value, dict):
        kind = str(value.get("kind") or value.get("part_kind") or "")
        if kind == "compaction" and value.get("content") is None and value.get("provider_details"):
            raise ValueError("Opaque provider-native compaction cannot be cross-provider projected")
        if kind == "attachment-ref":
            return {
                "kind": "attachment-ref", "id": str(value.get("id") or ""),
                "name": value.get("name"), "mime": value.get("mime"),
            }
        if kind in _MEDIA_KINDS:
            return {"kind": "text-content", "content": "[附件内容已省略；原始附件仍保留在会话事件中]"}
        return {
            item_key: _sanitize_value(item_value, key=item_key)
            for item_key, item_value in value.items()
            if item_key not in {"metadata", "provider_details", "vendor_details", "vendor_metadata"}
        }
    return value


def serialize_sanitized_projection(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for message in messages:
        payload = _sanitize_value(serialize_message(message))
        if not isinstance(payload, dict):
            raise ValueError("Serialized model message must be an object")
        deserialize_message(payload)
        payloads.append(payload)
    return payloads


__all__ = ["history_hash", "serialize_sanitized_projection"]
