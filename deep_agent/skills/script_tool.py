from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_ai import FunctionToolset, ModelRetry, RunContext, ToolFailed

from deep_agent.agent.deps import AgentDeps
from deep_agent.sandbox.backend import PythonExecutionBackend
from deep_agent.sandbox.types import (
    ExecuteRequest,
    JsonValue,
    MAX_CODE_BYTES,
    MAX_SCRIPT_RESOURCE_BYTES,
    MAX_SCRIPT_RESOURCE_FILES,
    SandboxInputFile,
)
from deep_agent.skills.runtime import ResolvedSkill


def _indexed_entry(
    item: ResolvedSkill,
    path: str,
    *,
    category: str | None = None,
) -> tuple[dict[str, Any], Path]:
    entry = next(
        (
            candidate
            for candidate in item.resource_index
            if candidate.get("path") == path
            and (category is None or candidate.get("category") == category)
        ),
        None,
    )
    if entry is None:
        label = f" {category}" if category else ""
        raise ModelRetry(f"Unknown{label} path {path!r} for Skill {item.code!r}")
    target = (item.package_root / path).resolve()
    root = item.package_root.resolve()
    if target != root and root not in target.parents:
        raise ModelRetry("Skill path is outside the installed package")
    if not target.is_file():
        raise ToolFailed(f"Skill file is missing: {path}")
    return entry, target


def _read_script(path: Path) -> str:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ToolFailed(f"Skill script could not be read: {path.name}") from exc
    if len(raw) > MAX_CODE_BYTES:
        raise ToolFailed(f"Skill script exceeds {MAX_CODE_BYTES} bytes: {path.name}")
    try:
        return raw.decode("utf-8")
    except UnicodeError as exc:
        raise ToolFailed(f"Skill script must be UTF-8 Python: {path.name}") from exc


def _resource_files(item: ResolvedSkill, paths: list[str]) -> tuple[SandboxInputFile, ...]:
    if len(paths) > MAX_SCRIPT_RESOURCE_FILES:
        raise ModelRetry(f"resource_paths accepts at most {MAX_SCRIPT_RESOURCE_FILES} files")
    if len(paths) != len(set(paths)):
        raise ModelRetry("resource_paths must not contain duplicates")

    files: list[SandboxInputFile] = []
    total = 0
    for path in paths:
        entry, target = _indexed_entry(item, path)
        if entry.get("category") == "script":
            raise ModelRetry("resource_paths may only contain References or Assets")
        try:
            raw = target.read_bytes()
        except OSError as exc:
            raise ToolFailed(f"Skill resource could not be read: {path}") from exc
        total += len(raw)
        if total > MAX_SCRIPT_RESOURCE_BYTES:
            raise ModelRetry(
                f"selected Skill resources exceed {MAX_SCRIPT_RESOURCE_BYTES} bytes"
            )
        files.append(SandboxInputFile.from_bytes(path, raw))
    return tuple(files)


def build_skill_script_toolset(
    scripts: dict[str, ResolvedSkill],
    *,
    backend: PythonExecutionBackend,
    timeout_sec: float,
) -> FunctionToolset[AgentDeps] | None:
    if not scripts:
        return None
    names = ", ".join(sorted(scripts))
    timeout_ms = int(timeout_sec * 1000)
    toolset = FunctionToolset[AgentDeps](
        id="skill-script-sandbox",
        instructions=(
            "Stored Skill scripts execute only in the configured isolated Python Sandbox. "
            "Use an exact script path listed by list_skill_resources. Provide explicit inputs, "
            "arguments and only the required same-Skill resources. A script returns its formal "
            "structured result through outputs/result.json. "
            f"Authorized Skills with executable scripts: {names}."
        ),
    )

    @toolset.tool
    async def execute_skill_script(
        ctx: RunContext[AgentDeps],
        skill_code: str,
        script_path: str,
        arguments: list[str] | None = None,
        inputs: dict[str, JsonValue] | None = None,
        resource_paths: list[str] | None = None,
    ) -> dict[str, object]:
        """Execute one authorized stored Python Skill script in the isolated Sandbox."""
        if ctx.deps.user is None:
            raise ModelRetry("Skill脚本执行需要已认证用户")
        item = scripts.get(skill_code)
        if item is None:
            raise ModelRetry(
                f"Skill {skill_code!r} has no executable script for this Agent. Choose one of: {names}."
            )
        entry, target = _indexed_entry(item, script_path, category="script")
        if Path(str(entry.get("path") or "")).suffix.lower() != ".py":
            raise ModelRetry("execute_skill_script only supports indexed UTF-8 .py scripts")

        code = _read_script(target)
        files = _resource_files(item, list(resource_paths or []))
        try:
            request = ExecuteRequest(
                request_id=uuid.uuid4().hex,
                mode="script",
                code=code,
                arguments=tuple(arguments or ()),
                inputs=inputs or {},
                files=files,
                timeout_ms=timeout_ms,
            )
        except ValidationError as exc:
            raise ModelRetry(f"Invalid Skill script execution request: {exc}") from exc
        result = await backend.execute(request)
        return result.model_dump(mode="json")

    return toolset
