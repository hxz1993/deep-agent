from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from pydantic import ValidationError
from pydantic_ai import FunctionToolset, ModelRetry, RunContext, ToolFailed, ToolReturn

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_errors import tool_error_message
from deep_agent.sandbox.backend import PythonExecutionBackend
from deep_agent.sandbox.file_bridge import SandboxFileInput, prepare_input_files, publish_execution_result
from deep_agent.files.upload_settings import load_upload_settings_for_tool
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
        raise ToolFailed(tool_error_message(f"Skill script could not be read: {path.name}", exc)) from exc
    if len(raw) > MAX_CODE_BYTES:
        raise ToolFailed(f"Skill script exceeds {MAX_CODE_BYTES} bytes: {path.name}")
    try:
        return raw.decode("utf-8")
    except UnicodeError as exc:
        raise ToolFailed(f"Skill script must be UTF-8 Python: {path.name}") from exc


def _verify_indexed_bytes(entry: dict[str, Any], raw: bytes) -> None:
    expected = entry.get("content_hash")
    if not expected or hashlib.sha256(raw).hexdigest() != expected:
        raise ToolFailed("Skill 文件与已解析版本不一致，请重新加载后再执行")


def _execution_bundle(item: ResolvedSkill, paths: list[str]) -> tuple[SandboxInputFile, ...]:
    # Work from the selected version's index. A runtime replacement must not mix
    # new modules or resources into an already resolved call.
    manifest = item.package_root / "SKILL.md"
    try:
        skill_text = manifest.read_bytes()
    except OSError as exc:
        raise ToolFailed("Skill SKILL.md 不可用") from exc
    if item.definition_hash is None or hashlib.sha256(skill_text).hexdigest() != item.definition_hash:
        raise ToolFailed("Skill SKILL.md 与已解析版本不一致，请重新加载后再执行")
    files = [SandboxInputFile.from_bytes("SKILL.md", skill_text)]
    for entry in item.resource_index:
        path = str(entry.get("path") or "")
        if entry.get("category") != "script" or Path(path).suffix.lower() != ".py":
            continue
        _, target = _indexed_entry(item, path, category="script")
        raw = _read_script(target).encode("utf-8")
        _verify_indexed_bytes(entry, raw)
        files.append(SandboxInputFile.from_bytes(path, raw))
    files.extend(_resource_files(item, paths))
    if sum(len(file.decoded()) for file in files) > MAX_SCRIPT_RESOURCE_BYTES:
        raise ModelRetry(f"Skill modules and resources exceed {MAX_SCRIPT_RESOURCE_BYTES} bytes")
    try:
        unchanged = manifest.read_bytes() == skill_text
    except OSError:
        unchanged = False
    if not unchanged:
        raise ToolFailed("Skill 在执行副本装配期间发生更新，请重新加载后再执行")
    return tuple(files)


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
            raise ToolFailed(tool_error_message(f"Skill resource could not be read: {path}", exc)) from exc
        _verify_indexed_bytes(entry, raw)
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
            "arguments and only the required same-Skill resources. Same-Skill Python modules "
            "are carried automatically under skill/ with the original entry path. The cwd "
            "contains inputs.json and outputs/. Resolve Skill assets relative to __file__. "
            "Use files for authorized session attachments and deepagent_files.input_path; "
            "publish_path registers generated files; the completed tool returns uploaded File IDs in files. "
            "A script returns its formal "
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
        files: list[SandboxFileInput] | None = None,
    ) -> ToolReturn:
        """Execute one authorized stored Python Skill script in the isolated Sandbox.

        Returns an execution envelope with status, result, files and truncated. The
        script's business fields are inside result, not at the envelope root.
        Check status == 'ok' and each truncated flag before using that result.
        With PTC, pass upstream JSON directly in inputs and keep the returned
        result in a variable for the next tool instead of transcribing it.
        """
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

        bundle = _execution_bundle(item, list(resource_paths or []))
        upload_settings = await load_upload_settings_for_tool(ctx)
        input_files = await prepare_input_files(
            ctx, files, upload_settings, tool_name="execute_skill_script", reserve_skill=True,
        )
        try:
            request = ExecuteRequest(
                request_id=uuid.uuid4().hex,
                mode="script",
                code="",
                script_entry=script_path,
                skill_files=bundle,
                arguments=tuple(arguments or ()),
                inputs=inputs or {},
                files=input_files,
                timeout_ms=timeout_ms,
            )
        except ValidationError as exc:
            errors = exc.errors(include_input=False, include_context=False, include_url=False)
            raise ModelRetry(f"Invalid Skill script execution request: {errors}") from exc
        result = await backend.execute(request)
        return await publish_execution_result(
            ctx, result, upload_settings, tool_name="execute_skill_script", input_files=input_files,
        )

    return toolset
