from __future__ import annotations

import keyword
import asyncio
import re
from dataclasses import replace
from typing import Any

from loguru import logger
from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.exceptions import ToolFailedError
from pydantic_ai.messages import ToolReturn, ToolReturnPart
from pydantic_ai.toolsets.abstract import ToolsetTool
from pydantic_ai_harness.code_mode import CodeModeToolset

from deep_agent.agent.deps import AgentDeps
from deep_agent.ptc.diagnostics import Collector, current_collector
from deep_agent.ptc.limits import PTC_WALL_TIMEOUT_SECONDS
from deep_agent.ptc.file_payload import (
    CODE_MODE_CODE_ONLY,
    CODE_MODE_HYBRID,
    CODE_MODE_METADATA_KEY,
    CODE_MODE_ORIGINAL_NAME_KEY,
    CODE_MODE_TRUSTED_RETURN_SCHEMA_KEY,
    FILE_DATA_TOKEN_PREFIX,
    payload_secrets,
    safe_audit_value,
    safe_result_value,
)

_RUN_CODE_NAME = "run_code"
_INVALID_IDENTIFIER = re.compile(r"\W")
_LONG_BASE64 = re.compile(r"(?<![A-Za-z0-9+/])[A-Za-z0-9+/]{256,}={0,2}(?![A-Za-z0-9+/])")
_DISCOVERY_HINT = (
    "Discovered tools are directly callable with their native schemas; "
    "discovery does not require run_code."
)

_USAGE_RULES = """

DeepAgent PTC rules:
- Default to ordinary native MCP calls. Having multiple dependent tools does not
  justify PTC: use native calls whenever the model must read, understand, validate,
  or choose based on the intermediate result.
- Reserve `run_code` for opaque file/binary relay or bounded data processing where
  intermediate rows/bytes do not need model interpretation (for example filtering,
  aggregation, or a known batch operation returning a compact summary).
- Tool discovery, schema inspection, connection/pairing, and canvas snapshots belong
  in ordinary native calls. Never use `run_code` to discover or probe available tools.
  `search_tools` is native-only: call it outside `run_code`, then read the disclosed
  ordinary tool schemas. Do not use `dir`, `globals`, or guessed aliases to inspect
  the sandbox or recover a missing tool.
- Catalog functions exist only inside `run_code`; never call a
  catalog alias as a direct outer tool. A function can be called directly only when the
  same protocol name is separately present in the ordinary tool list with its own schema.
- Every function listed in the Code Mode catalog is async. Call it with top-level
  `await function_name(...)`; never wrap it with `asyncio.run()`.
- Use a normal tool call when one call is enough and its arguments are already known.
- For file relay, discover the required schemas and select/verify the destination
  through native calls first. Only then use PTC to relay the opaque bytes between
  the known upload/send functions without exposing bytes to the model.
- A failed PTC snippet is a completed failed tool call, not a request to keep probing
  the sandbox. Prefer the available native tools next. Check already-started writes
  before any retry; a script error does not roll back earlier nested side effects.
- Use `load_file_data` only inside `run_code` when an authorized session file or a
  public HTTP(S) file must be passed unchanged to an MCP string argument. Provide
  exactly one of `file_id` and `url`. Treat the returned value as a mapping,
  access fields with `payload['name']`, and pass `payload['content']` unchanged to the
  next tool. Never inspect, measure, print, or return the `content` value.
- Return only the compact result needed for the task. Do not print file payloads,
  Base64, or large intermediate values.
- Authorized MCP, web_search, web_fetch, execute_python, execute_skill_script and
  export_file_to_download_center retain their ordinary native entry points. Only
  functions actually listed in this catalog can also be used here. Call Python
  with code and JSON inputs; call a Skill with its exact authorized script path.
  No save_file_data, read_file, parse_document, UI confirmation or Shell function
  is added to this catalog. Python executions still have separate temporary dirs.
- Internal returns go to the program without model-display pagination, up to
  32 MiB per return. Tool/source limits and incomplete-result markers still apply.
  Python/Skill inputs allow 16 MiB JSON. Monty memory is 256 MiB, its shared session
  execution allowance is 120 seconds, and each run_code also has a 120-second total
  deadline including tool waits and at most 100 calls. Reduce/aggregate over-limit
  data; do not assume the platform automatically pages the source or saves it.
- A Skill may return complete HTML/text for export_file_to_download_center with
  source.type='content'. A Python-generated authorized file may use 'platform_file'.
  Publish final deliverables only; return their genuine receipt, not the document.
- Execute side-effecting tools sequentially. After an uncertain failure, inspect the
  target state instead of blindly repeating the write. Never retry the same media send
  with the same uploaded id and unchanged arguments. If an image upload succeeds but
  the destination rejects a nonstandard format such as WebP, convert it with an
  available execute_python tool before one new relay attempt; otherwise use its
  ordinary native entry point. Do not repeat the rejected upload/send unchanged.
- `run_code` cannot directly access host files, environment variables, network sockets,
  Shell, pip, or third-party packages. It can call only catalog functions; public-file
  relay is available exclusively through `load_file_data(url=...)`.

Generic authorized-file relay shape:
```python
payload = await load_file_data(file_id="file_001", encoding="base64")
uploaded = await upload_tool(
    file_name=payload["name"],
    payload_string=payload["content"],
)
{"uploaded_id": uploaded["id"]}
```

Generic public-file relay shape:
```python
payload = await load_file_data(url="https://example.com/report.png")
uploaded = await upload_tool(
    file_name=payload["name"],
    payload_string=payload["content"],
)
{"uploaded_id": uploaded["id"]}
```

Common media-message relay shape:
```python
# target_chat_id was selected and verified through an earlier native call.
payload = await load_file_data(url=public_url)
uploaded = await upload_media(
    file_name=payload["name"],
    base64_content=payload["content"],
    type="image",
)
sent = await send_bot_message(
    chat_id=target_chat_id,
    msg_type="image",
    image={"media_id": uploaded["media_id"]},
)
{"success": sent.get("success")}
```
For WeCom AI-bot media relay, the corresponding protocol names are usually
`wecom-doc_media_upload` and `wecom-chat_message_aibot_send`. Disclose their schemas
through native search if needed, then use their sanitized Python aliases for the
opaque relay only. Never pass a public URL or
the opaque `payload["content"]` directly to the final message-send field. The WeCom
session-list result must be read through a native call to select and verify the target;
do not discover the destination or probe response shapes inside PTC.
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
    """Official CodeMode plus native MCP visibility and private diagnostics."""

    async def __aexit__(self, *args):
        from deep_agent.ptc.async_runtime import close_runtime
        try:
            await close_runtime(self)
        finally:
            await super().__aexit__(*args)

    @staticmethod
    def _return_metadata(ctx, collector):
        diagnostic = collector.safe_document()
        ctx.deps.ptc_diagnostics[collector.parent_id] = diagnostic
        metadata = {"code_mode": True, "ptc_diagnostics": diagnostic}
        if collector.generated_files:
            refs = list(collector.generated_files.values())
            ctx.deps.ptc_generated_files[collector.parent_id] = refs
            metadata["generated_files"] = refs
        else:
            ctx.deps.ptc_generated_files.pop(collector.parent_id, None)
        return metadata

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

        # Keep the original ToolSearch contract, not CodeMode's code-only discovery
        # nudge: Hybrid leaves newly disclosed MCP tools directly callable as well.
        for name, original in wrapped_tools.items():
            if original.tool_def.tool_kind == "tool-search" and name in result:
                result[name] = replace(
                    result[name],
                    tool_def=replace(
                        result[name].tool_def,
                        description=(original.tool_def.description or "") + "\n\n" + _DISCOVERY_HINT,
                    ),
                )

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
                    "- `await load_file_data(file_id: str | None = None, url: str | None "
                    "= None, encoding: Literal['base64', 'text'] = 'base64', "
                    "text_encoding: str = 'utf-8', max_chars: int = 65536) -> FileData` "
                    "(code-only authorized session/public-file primitive; provide exactly "
                    "one source and access "
                    "the returned mapping with `payload['name']` / `payload['content']`)"
                )
            elif isinstance(original, str) and original != alias:
                rows.append(f"- `{original}` -> Python `await {alias}(...)`")
            else:
                rows.append(f"- `await {alias}(...)`")
        return "\n".join(rows)

    def _partition_callable_tools(
        self,
        wrapped_tools: dict[str, ToolsetTool[AgentDeps]],
    ) -> tuple[dict[str, Any], dict[str, str]]:
        callable_defs, aliases = super()._partition_callable_tools(wrapped_tools)
        # MCP output schemas are third-party input. Only the existing evidence-backed
        # allowlist may shape Monty's generated return types; everything else remains
        # canonical JSON/Any rather than inventing a structure from a tool name.
        for alias, tool_def in list(callable_defs.items()):
            metadata = tool_def.metadata or {}
            if (
                metadata.get(CODE_MODE_METADATA_KEY) == CODE_MODE_HYBRID
                and not metadata.get(CODE_MODE_TRUSTED_RETURN_SCHEMA_KEY)
                and tool_def.return_schema is not None
            ):
                callable_defs[alias] = replace(tool_def, return_schema=None)
        return callable_defs, aliases

    async def call_tool(
        self,
        name: str,
        tool_args: dict[str, Any],
        ctx: RunContext[AgentDeps],
        tool: ToolsetTool[AgentDeps],
    ) -> Any:
        if name != _RUN_CODE_NAME:
            return await super().call_tool(name, tool_args, ctx, tool)

        secrets = payload_secrets(ctx.deps)
        collector = Collector(
            parent_id=ctx.tool_call_id or "", run_id=ctx.deps.run_id,
            session_id=ctx.deps.session_id, sensitive_values=secrets,
        )
        token = current_collector.set(collector)
        try:
            if tool_args.get("restart"):
                from deep_agent.ptc.async_runtime import reset_runtime
                await reset_runtime(self)
            async with asyncio.timeout(PTC_WALL_TIMEOUT_SECONDS):
                result = await super().call_tool(name, tool_args, ctx, tool)
        except (ModelRetry, TimeoutError) as exc:
            collector.finish("failed")
            metadata = self._return_metadata(ctx, collector)
            safe_message = _scrub_error_text(
                (f"PTC exceeded its {PTC_WALL_TIMEOUT_SECONDS}-second total time limit"
                 if isinstance(exc, TimeoutError) else str(exc)),
                sensitive_values=secrets,
            )
            # Preserve a real failed ToolReturnPart (including bounded audit) rather
            # than asking the model to retry code until the whole Agent run aborts.
            # This is the framework's native failure signal, not a successful result
            # with an application-only error flag. Cancellation/deferral is untouched.
            raise ToolFailedError(ToolReturnPart(
                tool_name=name,
                tool_call_id=ctx.tool_call_id or "",
                content={
                    "error": safe_audit_value(safe_message, sensitive_values=secrets),
                    "guidance": (
                        "PTC execution failed. Prefer ordinary native tools; search_tools, "
                        "schema discovery, pairing and snapshots must stay native. "
                        "Earlier nested writes may have succeeded: inspect target state "
                        "before retrying; do not repeat the same failed snippet."
                    ),
                },
                outcome="failed",
                metadata=metadata,
            )) from exc
        except asyncio.CancelledError:
            collector.finish("interrupted")
            self._return_metadata(ctx, collector)
            raise
        except Exception:
            collector.finish("failed")
            self._return_metadata(ctx, collector)
            raise
        else:
            collector.finish("completed")
            metadata = self._return_metadata(ctx, collector)
            if isinstance(result, ToolReturn):
                return ToolReturn(
                    return_value=safe_result_value(result.return_value, sensitive_values=secrets),
                    metadata=metadata,
                )
            return safe_result_value(result, sensitive_values=secrets)
        finally:
            current_collector.reset(token)

    async def _execute_code(self, code, ctx, run_code_tool, execution):
        collector = current_collector.get()
        if collector is not None:
            collector.execution = execution
        from deep_agent.ptc.async_runtime import execute_code
        return await execute_code(self, code, ctx, run_code_tool, execution)
