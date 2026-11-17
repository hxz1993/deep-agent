from __future__ import annotations

from typing import Any

from pydantic_ai import FunctionToolset, ModelRetry

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_names import READ_ONLY_TOOL
from deep_agent.files.toolset import skill_file_read_format
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
            "Text script source and helper modules can also be read with read_file; "
            "reading source does not grant script execution permissions. "
            f"Authorized Skills with resources: {names}."
        ),
    )

    def _item(skill_code: str) -> ResolvedSkill:
        item = resources.get(skill_code)
        if item is None:
            raise ModelRetry(
                f"Skill {skill_code!r} is not associated with this Agent. Choose one of: {names}."
            )
        return item

    @toolset.tool_plain(metadata={READ_ONLY_TOOL: True})
    async def list_skill_resources(skill_code: str) -> dict[str, Any]:
        """List References, Assets and scripts in one associated Skill without reading their content."""
        item = _item(skill_code)
        entries = []
        for entry in item.resource_index:
            kind = skill_file_read_format(entry)
            entries.append(
                {
                    "path": entry["path"],
                    "category": entry["category"],
                    "mime": entry["mime"],
                    "size": entry["size"],
                    "readable": kind is not None,
                    "read_format": kind,
                    "read_unit": {"text": "line", "docx": "block", "xlsx": "row", "pptx": "block", "pdf": "page"}.get(kind),
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
            "scripts_readable": any(entry["category"] == "script" and entry["readable"] for entry in entries),
        }

    return toolset
