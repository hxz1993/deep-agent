from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID


@dataclass
class AgentDeps:
    """Per-run dependencies injected into tools via ``RunContext[AgentDeps]``.

    Add request-scoped services here (db handles, http clients, the current
    user, feature flags). Tools declare ``ctx: RunContext[AgentDeps]`` to read them.
    """

    session_id: str
    user: str | None = None
    agent_id: UUID | None = None
