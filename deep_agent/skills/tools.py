from __future__ import annotations

from typing import Any

from pydantic_ai import FunctionToolset, ModelRetry

from deep_agent.agent.deps import AgentDeps
from deep_agent.skills.resource_reader import present_resource_entry
from deep_agent.skills.runtime import ResolvedSkill


def build_skill_resource_toolset(
    resources: dict[str, ResolvedSkill],
) -> FunctionToolset[AgentDeps] | None:
    if not resources:
        return None
    names = ", ".join(sorted(resources))
    toolset = FunctionToolset[AgentDeps](
        id="skill-resources",
        instructions=(
            "Skill references and assets are optional supporting material. "
            "Use list_skill_resources to discover exact paths, then use the system read_file tool "
            "with type=skill to read only the pages needed for the task. "
            "Script paths may be listed for discovery but cannot be read with read_file. "
            f"Authorized Skills with resources: {names}."
        ),
    )

    def _item(skill_code: str) -> ResolvedSkill:
        item = resources.get(skill_code)
        if item is None:
            raise ModelRetry(
                f"Skill {skill_code!r} is not enabled for this Agent. Choose one of: {names}."
            )
        return item

    @toolset.tool_plain
    async def list_skill_resources(skill_code: str) -> dict[str, Any]:
        """List References and Assets in one enabled Skill without reading their content."""
        item = _item(skill_code)
        entries = []
        for raw_entry in item.resource_index:
            entry = present_resource_entry(raw_entry)
            entries.append(
                {
                    "path": entry["path"],
                    "category": entry["category"],
                    "mime": entry["mime"],
                    "size": entry["size"],
                    "readable": bool(entry.get("readable")),
                    "read_format": entry.get("read_format"),
                    "read_unit": entry.get("read_unit"),
                    "content_hash": entry["content_hash"],
                }
            )
        script_count = sum(
            1 for entry in item.resource_index if entry.get("category") == "script"
        )
        return {
            "skill_code": skill_code,
            "resources": entries,
            "script_count": script_count,
            "scripts_readable": False,
        }

    return toolset
