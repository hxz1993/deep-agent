"""Codec for durable, replaceable in-flight Run snapshots.

These rows are not model history. Only their terminal Message Events are fed
back to a model; a snapshot is display/recovery state in the same Event Store.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from pydantic_ai.messages import ModelMessage

from deep_agent.db.serialization import deserialize_message, serialize_message

RUN_PROGRESS_EVENT_TYPE = "run/progress"
RUN_PROGRESS_TERMINAL_STATUSES = frozenset(
    {"completed", "pending", "interrupted", "cancelled", "failed"}
)


def run_progress_event_id(session_id: str, run_id: str) -> str:
    return uuid5(NAMESPACE_URL, f"deep-agent-run-progress:{session_id}:{run_id}").hex


def encode_run_progress(
    messages: Sequence[ModelMessage], *, first_message_ordinal: int, revision: int,
) -> dict[str, Any]:
    if type(first_message_ordinal) is not int or first_message_ordinal < 0:
        raise ValueError("Run progress message ordinal must be a nonnegative integer")
    if type(revision) is not int or revision < 0:
        raise ValueError("Run progress revision must be a nonnegative integer")
    return {
        "messages": [serialize_message(message) for message in messages],
        "first_message_ordinal": first_message_ordinal,
        "revision": revision,
    }


def decode_run_progress_messages(payload: Mapping[str, Any]) -> list[ModelMessage]:
    messages = payload.get("messages", [])
    if not isinstance(messages, list) or any(not isinstance(item, dict) for item in messages):
        raise ValueError("Run progress messages must be a list of serialized messages")
    return [deserialize_message(item) for item in messages]


def sealed_run_progress_payload(
    payload: Mapping[str, Any], *, message_count: int, recovered: bool = False,
) -> dict[str, Any]:
    """Retain a small receipt, not a permanent duplicate of the whole conversation."""
    result = {
        "revision": int(payload.get("revision", 0)),
        "first_message_ordinal": int(payload.get("first_message_ordinal", 0)),
        "message_count": message_count,
    }
    if recovered:
        result["error"] = {
            "code": "run_interrupted_by_restart",
            "message": "服务重启中断了本轮执行，已保留已产生的记录；工具不会自动重试。",
        }
    return result
