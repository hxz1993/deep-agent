from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Mapping

if TYPE_CHECKING:
    from deep_agent.agent.context.runtime import ContextRunState


@dataclass
class AgentDeps:
    """Per-run dependencies injected into tools via ``RunContext[AgentDeps]``.

    Add request-scoped services here (db handles, http clients, the current
    user, feature flags). Tools declare ``ctx: RunContext[AgentDeps]`` to read them.

    ``request_headers`` carries a whitelist of HTTP headers from the inbound client
    request. MCP toolsets read them when opening HTTP connections or when calling
    MCP tools (via connection headers and MCP ``_meta``).
    """

    session_id: str
    user: str | None = None
    agent_id: str | None = None
    request_headers: Mapping[str, str] = field(default_factory=dict)
    context_state: "ContextRunState | None" = None
