from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Mapping, Sequence

from deep_agent.schemas.attachment import AttachmentRef

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
    has_context_references: bool = False
    input_modalities: Sequence[str] = field(default_factory=tuple)
    attachment_by_id: dict[str, AttachmentRef] = field(default_factory=dict)
    attachment_bytes: dict[str, bytes] = field(default_factory=dict)
    attachment_parsed: dict[str, Any] = field(default_factory=dict)
    attachment_download_tasks: dict[str, asyncio.Task[bytes]] = field(
        default_factory=dict,
        repr=False,
    )
    attachment_parse_tasks: dict[str, asyncio.Task[Any]] = field(
        default_factory=dict,
        repr=False,
    )
