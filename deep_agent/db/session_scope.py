from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from deep_agent.db.conversation import SessionStore

NO_AGENT_SCOPE = ""


def normalize_agent_scope(agent_id: str | None) -> str:
    return agent_id or NO_AGENT_SCOPE


def agent_scope_to_api(agent_scope: str) -> str | None:
    return agent_scope or None


def split_legacy_session_key(session_key: str) -> tuple[str, str]:
    """Split legacy ``agent_id:session_id`` storage keys."""
    if ":" in session_key:
        agent_part, session_id = session_key.split(":", 1)
        return agent_part or NO_AGENT_SCOPE, session_id
    return NO_AGENT_SCOPE, session_key


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
    scope = normalize_agent_scope(agent_id)
    sessions = await store.list_sessions()
    target = (scope, session_id)
    if target in sessions:
        return target

    matches = [ref for ref in sessions if ref[1] == session_id]
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    if scope != NO_AGENT_SCOPE:
        for ref in matches:
            if ref[0] == scope:
                return ref
    return None
