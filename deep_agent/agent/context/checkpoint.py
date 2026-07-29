from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from loguru import logger
from pydantic_ai.messages import ModelMessage
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.db.serialization import deserialize_message, serialize_message
from deep_agent.db.stores.context_checkpoint import (
    ContextCheckpointSnapshot,
    ContextCheckpointStoreBase,
)

PAYLOAD_SCHEMA_VERSION = 2
_SENSITIVE_KEYS = {
    "authorization",
    "cookie",
    "headers",
    "api_key",
    "apikey",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "x-user-token",
}
_MEDIA_KINDS = {
    "audio-url",
    "binary",
    "document-url",
    "image-url",
    "video-url",
}
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|token|cookie)"
    r"(\s*[:=]\s*)([^\s,;\"']+)"
)
_SIGNED_URL_RE = re.compile(
    r"(?i)^https?://\S+[?&](?:token|signature|sig|x-amz-signature|x-goog-signature)="
)


@dataclass(frozen=True)
class ProjectionLoad:
    messages: list[ModelMessage]
    checkpoint: ContextCheckpointSnapshot | None
    used_checkpoint: bool
    invalid_reason: str | None = None


def history_hash(messages: list[ModelMessage]) -> str:
    payload = [serialize_message(message) for message in messages]
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def projection_fingerprint(
    *,
    llm_model_id: str | None,
    context_window_tokens: int | None,
    trigger_ratio: float,
    target_ratio: float,
    keep_tool_pairs: int,
    summary_model_id: str | None,
    max_output_tokens: int | None = None,
    estimator_source: str | None = None,
) -> str:
    payload = {
        "policy": "deep-agent-tiered-v2",
        "payload_schema_version": PAYLOAD_SCHEMA_VERSION,
        "llm_model_id": llm_model_id,
        "context_window_tokens": context_window_tokens,
        "max_output_tokens": max_output_tokens,
        "trigger_ratio": trigger_ratio,
        "target_ratio": target_ratio,
        "keep_tool_pairs": keep_tool_pairs,
        "summary_model_id": summary_model_id,
        "estimator_source": estimator_source,
        "summary_policy": "main-model-safe-rolling-v1",
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


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
        kind = str(value.get("kind") or "")
        if kind == "attachment-ref":
            return {
                "kind": "attachment-ref",
                "id": str(value.get("id") or ""),
                "name": value.get("name"),
                "mime": value.get("mime"),
            }
        if kind in _MEDIA_KINDS:
            return {
                "kind": "text-content",
                "content": "[附件内容已省略；原始附件仍保留在会话历史中]",
            }
        sanitized: dict[str, Any] = {}
        for item_key, item_value in value.items():
            if item_key in {
                "metadata",
                "provider_details",
                "vendor_details",
                "vendor_metadata",
            }:
                continue
            sanitized[item_key] = _sanitize_value(item_value, key=item_key)
        return sanitized
    return value


def serialize_sanitized_projection(messages: list[ModelMessage]) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for message in messages:
        payload = _sanitize_value(serialize_message(message))
        if not isinstance(payload, dict):
            raise ValueError("Serialized model message must be an object")
        # Validate the sanitized payload before it can become a checkpoint.
        deserialize_message(payload)
        payloads.append(payload)
    return payloads


def deserialize_projection(payloads: list[dict[str, Any]]) -> list[ModelMessage]:
    return [deserialize_message(payload) for payload in payloads]


async def load_projection(
    store: ContextCheckpointStoreBase,
    raw_history: list[ModelMessage],
    *,
    session_id: str,
    agent_id: str | None,
    fingerprint: str,
) -> ProjectionLoad:
    checkpoint = await store.get(session_id, agent_id=agent_id)
    if checkpoint is None:
        return ProjectionLoad(raw_history, None, False)
    if checkpoint.payload_schema_version != PAYLOAD_SCHEMA_VERSION:
        return ProjectionLoad(raw_history, checkpoint, False, "payload_schema_version")
    if checkpoint.projection_fingerprint != fingerprint:
        return ProjectionLoad(raw_history, checkpoint, False, "projection_fingerprint")
    if checkpoint.source_last_sequence < 0:
        return ProjectionLoad(raw_history, checkpoint, False, "source_last_sequence")
    source_count = checkpoint.source_last_sequence + 1
    if source_count > len(raw_history):
        return ProjectionLoad(raw_history, checkpoint, False, "history_truncated")
    if history_hash(raw_history[:source_count]) != checkpoint.source_history_hash:
        return ProjectionLoad(raw_history, checkpoint, False, "history_hash")
    try:
        projected = deserialize_projection(checkpoint.payload)
    except Exception as exc:  # corrupt derived state must never block the conversation
        logger.warning(
            "Ignoring invalid context checkpoint for {}/{}: {}",
            agent_id,
            session_id,
            type(exc).__name__,
        )
        return ProjectionLoad(raw_history, checkpoint, False, "payload_invalid")
    return ProjectionLoad(
        [*projected, *raw_history[source_count:]],
        checkpoint,
        True,
    )


async def save_projection(
    store: ContextCheckpointStoreBase,
    *,
    raw_history: list[ModelMessage],
    projected_history: list[ModelMessage],
    session_id: str,
    agent_id: str | None,
    fingerprint: str,
    expected_row_version: int,
    summary_model_id: str | None,
    estimator: TokenEstimator | None = None,
) -> bool:
    if not raw_history:
        return False
    payload = serialize_sanitized_projection(projected_history)
    return await store.save_if_version(
        session_id,
        agent_id=agent_id,
        expected_row_version=expected_row_version,
        source_last_sequence=len(raw_history) - 1,
        source_history_hash=history_hash(raw_history),
        payload=payload,
        payload_schema_version=PAYLOAD_SCHEMA_VERSION,
        projection_fingerprint=fingerprint,
        estimated_tokens=(estimator or TokenEstimator.conservative()).messages(
            projected_history
        ),
        summary_model_id=summary_model_id,
    )
