from __future__ import annotations

from uuid import UUID, uuid4

AGENT_ID_LENGTH = 36


def new_agent_id() -> str:
    """Return a canonical hyphenated UUID string for a new agent row."""
    return str(uuid4())


def normalize_agent_id(value: str | UUID) -> str:
    """Normalize arbitrary UUID input to canonical hyphenated lowercase string."""
    return str(UUID(str(value)))
