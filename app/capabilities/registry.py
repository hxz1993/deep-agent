from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.core.exceptions import CapabilityError


@dataclass(frozen=True)
class ToolInfo:
    """Lightweight, display-only description of a single tool."""

    name: str
    description: str = ""


@dataclass
class Capability:
    """A unit of extensible agent ability.

    Every extension point in deep-agent reduces to a Capability that wraps a
    Pydantic AI toolset:
      - native:       a ``FunctionToolset`` of in-process Python functions
      - skill:        instructions + a ``FunctionToolset`` loaded from ``skills/``
      - mcp:          an MCP server toolset loaded from ``config/mcp.json``
      - cursor-skill: a Cursor ``SKILL.md`` exposed via progressive disclosure

    ``toolset`` is any Pydantic AI ``AbstractToolset`` (typed as ``Any`` to avoid
    coupling to internal class paths). Instructions are carried by the toolset
    itself, so enabling a capability automatically injects its guidance.

    ``default_enabled`` controls whether the capability is included when an agent
    requests "all" capabilities (``names=None``). Capabilities that should only
    activate when named explicitly (e.g. a Cursor skill with
    ``disable-model-invocation: true``) set this to ``False``; they are still
    usable when listed by name.
    """

    name: str
    kind: str  # "native" | "skill" | "mcp" | "cursor-skill"
    toolset: Any
    description: str = ""
    tools: list[ToolInfo] = field(default_factory=list)
    default_enabled: bool = True


class CapabilityRegistry:
    """Holds all registered capabilities and hands their toolsets to agents."""

    def __init__(self) -> None:
        self._caps: dict[str, Capability] = {}

    def register(self, cap: Capability) -> None:
        if cap.name in self._caps:
            raise CapabilityError(f"Duplicate capability name: {cap.name}")
        self._caps[cap.name] = cap

    def replace_by_kind(self, kind: str, caps: list[Capability]) -> None:
        """Remove all capabilities of ``kind`` and register ``caps`` in their place."""
        self._caps = {name: cap for name, cap in self._caps.items() if cap.kind != kind}
        for cap in caps:
            self.register(cap)

    def names(self) -> list[str]:
        return list(self._caps)

    def get(self, name: str) -> Capability:
        if name not in self._caps:
            raise CapabilityError(f"Unknown capability: {name}")
        return self._caps[name]

    def toolsets(self, names: list[str] | None = None) -> list[Any]:
        """Return toolsets for the named capabilities.

        When ``names`` is None, only capabilities with ``default_enabled=True`` are
        included. When ``names`` is given, those capabilities are returned even if
        they are not enabled by default (explicit selection overrides the flag).
        """
        if names is None:
            caps = [c for c in self._caps.values() if c.default_enabled]
        else:
            caps = [self._caps[n] for n in names if n in self._caps]
        return [c.toolset for c in caps]

    def describe(self) -> list[dict[str, Any]]:
        return [
            {
                "name": c.name,
                "kind": c.kind,
                "description": c.description,
                "default_enabled": c.default_enabled,
                "tools": [{"name": t.name, "description": t.description} for t in c.tools],
            }
            for c in self._caps.values()
        ]
