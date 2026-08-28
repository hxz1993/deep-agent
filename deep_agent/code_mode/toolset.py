from __future__ import annotations

import keyword
import re
from dataclasses import replace
from typing import Any

from loguru import logger
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ToolReturn
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai_harness.code_mode import CodeModeToolset

from deep_agent.agent.deps import AgentDeps
from deep_agent.code_mode.file_payload import (
    CODE_MODE_CODE_ONLY,
    CODE_MODE_HYBRID,
    CODE_MODE_METADATA_KEY,
    CODE_MODE_ORIGINAL_NAME_KEY,
    FILE_DATA_TOKEN_PREFIX,
    payload_secrets,
    safe_audit_value,
)

_RUN_CODE_NAME = "run_code"
_INVALID_IDENTIFIER = re.compile(r"\W")
_LONG_BASE64 = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{256,}={0,2}(?![A-Za-z0-9+/])")

_USAGE_RULES = """

DeepAgent hybrid rules:
- Every function listed in the Code Mode catalog is async. Call it with top-level
  `await function_name(...)`; never wrap it with `asyncio.run()`.
- Use a normal tool call when one call is enough and its arguments are already known.
- Prefer `run_code` over multiple native calls whenever one tool's result feeds another
  tool. A native call is for one independent invocation; do not expose an intermediate
  result to the model just to pass one of its fields into the next tool.
- After `search_tools` reveals the functions required by a dependency chain, execute the
  remaining chain in one `run_code` call. Also use `run_code` for bounded concurrency,
  loops, filtering, sorting, grouping, aggregation, or conditional branches.
- Use `load_file_data` only inside `run_code` when an authorized session file must be
  passed directly to an MCP string argument. Treat the returned value as a mapping,
  access fields with `payload['name']`, and pass `payload['content']` unchanged to the
  next tool. Never inspect, measure, print, or return the `content` value.
- Return only the compact result needed for the task. Do not print file payloads,
  Base64, or large intermediate values.
- Execute side-effecting tools sequentially. After an uncertain failure, inspect the
  target state instead of blindly repeating the write.
- `run_code` cannot access host files, environment variables, network sockets, Shell,
  pip, or third-party packages. It can call only the functions listed in its catalog.

Generic dependency-chain shape (replace the abstract function names and arguments with
tools from the current catalog; do not copy them literally):
```python
source = await first_tool(query="...")
result = await second_tool(source_id=source["id"])
{"status": result["status"]}
```

Generic authorized-file relay shape:
```python
payload = await load_file_data(file_id="file_001", encoding="base64")
uploaded = await upload_tool(
    file_name=payload["name"],
    payload_string=payload["content"],
)
{"uploaded_id": uploaded["id"]}
```
""".rstrip()


def sanitize_python_name(name: str) -> str:
    safe = _INVALID_IDENTIFIER.sub("_", name)
    if safe and safe[0].isdigit():
        safe = f"_{safe}"
    if keyword.iskeyword(safe):
        safe = f"{safe}_"
    return safe


def _code_mode_kind(tool: ToolsetTool[AgentDeps]) -> str | None:
    metadata = tool.tool_def.metadata or {}
    value = metadata.get(CODE_MODE_METADATA_KEY)
    return value if isinstance(value, str) else None


def _scrub_error_text(value: str, *, sensitive_values: frozenset[str]) -> str:
    for secret in sensitive_values:
        if secret:
            value = value.replace(secret, "[file-payload-redacted]")
    value = re.sub(
        re.escape(FILE_DATA_TOKEN_PREFIX) + r"[A-Za-z0-9]+",
        "[file-data-token]",
        value,
    )
    value = re.sub(
        r"<extra_identity_context>.*?</extra_identity_context>",
        "[identity-context-redacted]",
        value,
        flags=re.DOTALL,
    )
    return _LONG_BASE64.sub("[base64-redacted]", value)


class HybridCodeModeToolset(CodeModeToolset[AgentDeps]):
    """Official CodeMode plus native MCP visibility and bounded audit projection."""

    async def get_tools(
        self,
        ctx: RunContext[AgentDeps],
    ) -> dict[str, ToolsetTool[AgentDeps]]:
        wrapped_tools = await self.wrapped.get_tools(ctx)
        candidates = {
            name: tool
            for name, tool in wrapped_tools.items()
            if _code_mode_kind(tool) in {CODE_MODE_HYBRID, CODE_MODE_CODE_ONLY}
        }

        aliases: dict[str, list[str]] = {}
        for name in candidates:
            aliases.setdefault(sanitize_python_name(name), []).append(name)
        collisions = {
            name
            for names in aliases.values()
            if len(names) > 1
            for name in names
        }
        for alias, names in aliases.items():
            if len(names) > 1:
                logger.warning(
                    "Code mode alias collision alias={} tools={}; keeping native only",
                    alias,
                    sorted(names),
                )

        self.tool_selector = sorted(set(candidates) - collisions)
        result = await super().get_tools(ctx)

        # A code-only helper must never fall back to the native catalog, including
        # the defensive alias-collision path where official CodeMode leaves it
        # unsandboxed.
        for name, exposed_tool in list(result.items()):
            if _code_mode_kind(exposed_tool) == CODE_MODE_CODE_ONLY:
                result.pop(name)

        # Official CodeMode removes selected tools from the native catalog. Hybrid
        # MCP tools deliberately remain available through both channels.
        for name, tool in wrapped_tools.items():
            if (
                _code_mode_kind(tool) == CODE_MODE_HYBRID
                and ctx.is_tool_available(tool.tool_def)
            ):
                result[name] = tool

        run_code = result.get(_RUN_CODE_NAME)
        if run_code is not None:
            result[_RUN_CODE_NAME] = replace(
                run_code,
                tool_def=replace(
                    run_code.tool_def,
                    description=(run_code.tool_def.description or "") + _USAGE_RULES,
                ),
            )
        return result

    @staticmethod
    def _render_catalog(callable_defs: dict[str, Any]) -> str:
        if not callable_defs:
            return ""
        rows = [
            "Functions available inside `run_code`. Every listed function is async: "
            "call it with top-level `await`, never with `asyncio.run()`. Read each "
            "ordinary tool schema for parameters and semantics:"
        ]
        for alias, tool_def in sorted(callable_defs.items()):
            metadata = tool_def.metadata or {}
            original = metadata.get(CODE_MODE_ORIGINAL_NAME_KEY)
            if alias == "load_file_data":
                rows.append(
                    "- `await load_file_data(file_id: str, encoding: Literal['base64', 'text'] "
                    "= 'base64', text_encoding: str = 'utf-8', max_chars: int = 65536) "
                    "-> FileData` (code-only authorized session-file primitive; access "
                    "the returned mapping with `payload['name']` / `payload['content']`)"
                )
            elif isinstance(original, str) and original != alias:
                rows.append(f"- `{original}` -> Python `await {alias}(...)`")
            else:
                rows.append(f"- `await {alias}(...)`")
        return "\n".join(rows)

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDeps],
        tool: ToolsetTool[AgentDeps],
    ) -> Any:
        if name != _RUN_CODE_NAME:
            return await super().call_tool(name, tool_args, ctx, tool)

        audit_start = len(ctx.deps.code_mode_audit)
        try:
            result = await super().call_tool(name, tool_args, ctx, tool)
        except ModelRetry as exc:
            safe_message = _scrub_error_text(
                str(exc),
                sensitive_values=payload_secrets(ctx.deps),
            )
            raise ModelRetry(safe_message) from exc

        nested_calls = ctx.deps.code_mode_audit[audit_start:]
        secrets = payload_secrets(ctx.deps)
        if isinstance(result, ToolReturn):
            return ToolReturn(
                return_value=safe_audit_value(
                    result.return_value,
                    sensitive_values=secrets,
                ),
                metadata={
                    "code_mode": True,
                    "nested_calls": safe_audit_value(
                        nested_calls,
                        sensitive_values=secrets,
                    ),
                },
            )
        return safe_audit_value(result, sensitive_values=secrets)
