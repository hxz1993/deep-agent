from __future__ import annotations

from uuid import UUID, uuid4

AGENT_ID_LENGTH = 36
PROVIDER_ID_LENGTH = 32


def new_agent_id() -> str:
    """Return a canonical hyphenated UUID string for a new agent row."""
    return str(uuid4())


def normalize_agent_id(value: str | UUID) -> str:
    """Normalize arbitrary UUID input to canonical hyphenated lowercase string."""
    return str(UUID(str(value)))


def new_provider_id() -> str:
    """Return a compact lowercase UUID hex string for a new provider row."""
    return uuid4().hex


def new_event_id() -> str:
    return uuid4().hex


def new_turn_id() -> str:
    return uuid4().hex


def normalize_provider_id(value: str | UUID) -> str:
    """Normalize compact or hyphenated UUID input to a 32-character hex id."""
    return UUID(str(value)).hex
