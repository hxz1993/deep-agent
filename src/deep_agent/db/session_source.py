"""Immutable session creation source; no historical inference or writes."""
from sqlalchemy import or_
from deep_agent.db.models import ChatSession

from deep_agent.schemas.session_source import CHANNEL_TYPES

def channel_source_type(channel_type: str) -> str:
    if channel_type not in CHANNEL_TYPES:
        raise ValueError("Unsupported channel source type")
    return f"channel:{channel_type}"

def normalize_session_source(source_type: str | None, source_id: str | None) -> tuple[str, str | None]:
    return (source_type, source_id) if source_type else ("internal", None)

def session_source_conditions(source_type: str | None = None, source_id: str | None = None) -> list:
    conditions = []
    if source_type == "internal":
        conditions.append(or_(ChatSession.source_type == "internal", ChatSession.source_type.is_(None)))
    elif source_type is not None:
        conditions.append(ChatSession.source_type == source_type)
    if source_id is not None:
        conditions.append(ChatSession.source_id == source_id)
    return conditions
