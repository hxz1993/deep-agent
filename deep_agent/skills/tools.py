from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from pydantic_ai import FunctionToolset, ModelRetry, ToolFailed

from deep_agent.agent.deps import AgentDeps
from deep_agent.skills.resource_reader import (
    SkillResourceReadError,
    present_resource_entry,
    read_resource_page,
)
from deep_agent.skills.runtime import ResolvedSkill


def _safe_resource_path(item: ResolvedSkill, path: str) -> tuple[dict[str, Any], Path]:
    entry = next(
        (
            candidate
            for candidate in item.resource_index
            if candidate.get("path") == path and candidate.get("category") != "script"
        ),
        None,
    )
    if entry is None:
        raise ModelRetry(f"Unknown resource {path!r} for Skill {item.code!r}")
    target = (item.package_root / path).resolve()
    root = item.package_root.resolve()
    if target != root and root not in target.parents:
        raise ModelRetry("Skill resource path is outside the installed package")
    if not target.is_file():
        raise ToolFailed(f"Skill resource is missing: {path}")
    return entry, target


def build_skill_resource_toolset(
    resources: dict[str, ResolvedSkill],
    *,
    scripts_executable: bool = False,
) -> FunctionToolset[AgentDeps] | None:
    if not resources:
        return None
    names = ", ".join(sorted(resources))
    script_instruction = (
        "Use execute_skill_script when an authorized stored script is required. "
        if scripts_executable
        else "Stored scripts are not executable in the current runtime. "
    )
    toolset = FunctionToolset[AgentDeps](
        id="skill-resources",
        instructions=(
            "Skill references and assets are optional supporting material. "
            "List resources before reading one, and read only the pages needed for the task. "
            "Script paths may be listed for discovery but cannot be read with read_skill_resource. "
            f"{script_instruction}"
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
                    "executable": bool(
                        entry.get("category") == "script" and scripts_executable
                    ),
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
            "scripts_executable": scripts_executable,
        }

    @toolset.tool_plain
    async def read_skill_resource(
        skill_code: str,
        path: str,
        offset: int | None = None,
        limit: int | None = None,
        sheet: str | None = None,
    ) -> dict[str, Any]:
        """Read an authorized text, DOCX or XLSX Skill resource using typed pagination."""
        item = _item(skill_code)
        entry, target = _safe_resource_path(item, path)
        try:
            page = await asyncio.to_thread(
                read_resource_page,
                entry,
                target,
                offset=offset,
                limit=limit,
                sheet=sheet,
            )
        except SkillResourceReadError as exc:
            if exc.correctable:
                raise ModelRetry(str(exc)) from exc
            raise ToolFailed(str(exc)) from exc
        return {
            "skill_code": skill_code,
            "path": path,
            "format": page.format,
            "unit": page.unit,
            "content": page.content,
            "metadata": page.metadata,
            "offset": page.offset,
            "next_offset": page.next_offset,
            "has_more": page.has_more,
            "returned_count": page.returned_count,
            "total_count": page.total_count,
            "truncated_by": page.truncated_by,
            "content_hash": entry["content_hash"],
        }

    return toolset
