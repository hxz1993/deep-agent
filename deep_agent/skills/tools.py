from __future__ import annotations

from pathlib import Path
from typing import Any

from pydantic_ai import FunctionToolset, ModelRetry

from deep_agent.agent.deps import AgentDeps
from deep_agent.skills.runtime import ResolvedSkill


MAX_SKILL_RESOURCE_PAGE = 32 * 1024


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
        raise ModelRetry(f"Skill resource is missing: {path}")
    return entry, target


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
            "List resources before reading one, and read only the pages needed for the task. "
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
        entries = [
            {
                "path": entry["path"],
                "category": entry["category"],
                "mime": entry["mime"],
                "size": entry["size"],
                "readable": bool(entry.get("readable")),
                "content_hash": entry["content_hash"],
            }
            for entry in item.resource_index
            if entry.get("category") != "script"
        ]
        return {"skill_code": skill_code, "resources": entries}

    @toolset.tool_plain
    async def read_skill_resource(
        skill_code: str,
        path: str,
        offset: int = 0,
        limit: int = MAX_SKILL_RESOURCE_PAGE,
    ) -> dict[str, Any]:
        """Read one authorized text Reference or Asset using character pagination."""
        item = _item(skill_code)
        entry, target = _safe_resource_path(item, path)
        if not entry.get("readable"):
            raise ModelRetry(
                f"binary_resource_not_readable: {path!r} can be downloaded by an administrator "
                "but cannot be inserted into model context"
            )
        if offset < 0:
            raise ModelRetry("offset must be non-negative")
        if limit < 1 or limit > MAX_SKILL_RESOURCE_PAGE:
            raise ModelRetry(f"limit must be between 1 and {MAX_SKILL_RESOURCE_PAGE}")
        try:
            content = target.read_text("utf-8")
        except UnicodeError as exc:
            raise ModelRetry(f"Skill resource is not valid UTF-8: {path}") from exc
        page = content[offset : offset + limit]
        next_offset = offset + len(page)
        return {
            "skill_code": skill_code,
            "path": path,
            "content": page,
            "offset": offset,
            "next_offset": next_offset,
            "has_more": next_offset < len(content),
            "content_hash": entry["content_hash"],
        }

    return toolset
