from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from deep_agent.capabilities.registry import Capability, ToolInfo


@dataclass
class Skill:
    """A higher-level, packaged capability.

    A skill bundles usage ``instructions`` (injected into the agent's prompt)
    with a Pydantic AI ``FunctionToolset`` of related tools. Put the instructions
    on the toolset (via ``FunctionToolset(instructions=...)``) so they are only
    active when the skill is enabled.

    Author one by creating ``skills/<name>/__init__.py`` that defines a module
    level ``SKILL = Skill(...)``.
    """

    name: str
    description: str
    toolset: Any  # pydantic_ai.FunctionToolset
    tools: list[ToolInfo] = field(default_factory=list)

    def to_capability(self) -> Capability:
        return Capability(
            name=f"skill:{self.name}",
            kind="skill",
            toolset=self.toolset,
            description=self.description,
            tools=self.tools,
        )
