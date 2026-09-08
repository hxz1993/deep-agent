from __future__ import annotations

import uuid


def conversation_id_for(agent_id: str | None, session_id: str) -> str:
    """Return the stable Pydantic AI conversation id for a DeepAgent session."""

    return f"deepagent:{agent_id or 'anonymous'}:{session_id}"


def new_run_id() -> str:
    """Mint a caller-owned run id before the first SSE event is published."""

    return uuid.uuid4().hex
