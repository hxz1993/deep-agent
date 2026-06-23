from __future__ import annotations

from pydantic import TypeAdapter
from pydantic_ai.messages import ModelMessage

_message_adapter: TypeAdapter[ModelMessage] = TypeAdapter(ModelMessage)


def serialize_message(message: ModelMessage) -> dict:
    """JSON-compatible dict for DB persistence."""
    return _message_adapter.dump_python(message, mode="json")


def deserialize_message(payload: dict) -> ModelMessage:
    return _message_adapter.validate_python(payload)
