from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deep_agent.db.conversation import SessionStore

NO_AGENT_SCOPE = ""


def normalize_agent_scope(agent_id: str | None) -> str:
    return agent_id or NO_AGENT_SCOPE


def agent_scope_to_api(agent_scope: str) -> str | None:
    return agent_scope or None


def runtime_session_key(session_id: str, *, agent_id: str | None = None) -> str:
    """Process-local key for locks and MCP pools."""
    scope = normalize_agent_scope(agent_id)
    if scope == NO_AGENT_SCOPE:
        return session_id
    return f"{scope}:{session_id}"


async def resolve_session(
    store: SessionStore,
    session_id: str,
    *,
    agent_id: str | None = None,
) -> tuple[str, str] | None:
    """Resolve API session id to ``(agent_scope, session_id)``."""
    return await store.resolve_ref(session_id, agent_id=agent_id)
