from __future__ import annotations

import re

from loguru import logger

from pydantic_ai import FunctionToolset

from deep_agent.capabilities.cursor_skills.discovery import SkillEntry, discover_cursor_skills
from deep_agent.capabilities.registry import Capability, ToolInfo

# Cap a loaded body so a large SKILL.md cannot blow up the context window.
_BODY_LIMIT = 8000


def _sanitize(name: str) -> str:
    """Turn a skill name into a valid, collision-free tool-name suffix."""
    return re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")


def _build_capability(skill: SkillEntry) -> Capability:
    sane = _sanitize(skill.name)
    read_tool = f"read_{sane}"
    body = skill.body  # captured per-skill by the closure below

    toolset = FunctionToolset(
        instructions=(
            f"Skill '{skill.name}': {skill.description} "
            f"Call {read_tool}() to load its full instructions before acting on this task."
        )
    )

    def read_skill() -> str:
        return body[:_BODY_LIMIT]

    toolset.tool_plain(
        name=read_tool,
        description=f"Load the full instructions for the '{skill.name}' skill.",
    )(read_skill)

    return Capability(
        name=f"cursor-skill:{skill.name}",
        kind="cursor-skill",
        toolset=toolset,
        description=skill.description,
        tools=[ToolInfo(read_tool, f"Load full instructions for the '{skill.name}' skill.")],
        default_enabled=skill.auto_invoke,
    )


def load_cursor_skill_capabilities(skills_dirs: str) -> list[Capability]:
    """Build one ``cursor-skill:<name>`` capability per discovered SKILL.md."""
    capabilities = [_build_capability(skill) for skill in discover_cursor_skills(skills_dirs)]
    for cap in capabilities:
        logger.info("Loaded cursor skill {!r} (default_enabled={})", cap.name, cap.default_enabled)
    return capabilities
