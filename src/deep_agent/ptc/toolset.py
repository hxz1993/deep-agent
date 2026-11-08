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
from pydantic_monty import MontyRuntimeError

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
    "discovery does not require run_code. For dependent calls or bulk result "
    "processing, use their listed run_code aliases to pass values directly."
)

_ROUTING_GUIDANCE = """Use `run_code` for bounded tool orchestration and data processing: keep tool results
in program variables when they feed the next tool, or batch/filter/aggregate data.
Keep intermediate data in code until the model needs it for a decision. Choose
the steps from the actual task; no fixed recipe is required.
For one independent call with a small result used directly in the answer, use its
native tool. For libraries or file creation, call execute_python/execute_skill_script,
either natively or as a step here.
Python syntax alone does not require execute_python; strings, lists, loops and
conditions can process JSON/text values here.
"""

_EXECUTION_GUIDANCE = """Execute bounded Python code in Monty. REPL state persists within the same Run;
restart: true clears it. The last expression is returned and print output is captured.
"""

_USAGE_RULES = """

DeepAgent PTC rules:
- Check each tool's success and completeness markers. For undocumented JSON,
  inspect a small sample before assuming field names or types. Normalize nullable
  or mixed-type fields before arithmetic. Use a loop with a numeric accumulator
  for simple totals; avoid sum or += on raw fields. Treat missing values as zero
  only when the task defines that rule.
- Monty runs a Python subset, not full CPython. Custom JSON encoder classes (`cls`)
  and json.loads object_hook/object_pairs_hook are unsupported; transform parsed
  data explicitly or use an authorized execute_python/Skill for custom codecs.
- For concurrent read-only calls use `await asyncio.gather(a(), b())` without
  keyword arguments; `return_exceptions=True` is unsupported. For per-call error
  handling, catch exceptions explicitly. Do not use create_task, wait_for,
  or as_completed; they are unavailable in this environment.
- Do not use `yield`/`yield from`. Generator expressions eagerly produce lists,
  not lazy iterators; prefer ordinary loops or list comprehensions within budgets.
  Standard-library APIs are partial; use an authorized execute_python/Skill for
  unavailable APIs and third-party libraries rather than installing packages here.
- Use a native call for an independent small lookup/action. If fresh model interpretation or semantic judgment
  is needed, return compact candidates for the model to decide. When a tool result feeds
  the next tool, pass it directly here and check success/integrity in code.
- Tool discovery and schema inspection belong in ordinary native calls.
  Never use `run_code` to discover or probe available tools.
  `search_tools` is native-only: call it outside `run_code`, then read the disclosed
  ordinary tool schemas. Do not use `dir`, `globals`, or guessed aliases to inspect
  the sandbox or recover a missing tool.
- Catalog functions exist only inside `run_code`; never call a
  catalog alias as a direct outer tool. A function can be called directly only when the
  same protocol name is separately present in the ordinary tool list with its own schema.
- Follow the call form shown in the Code Mode catalog: use top-level `await` for
  an async function and call a listed synchronous function directly;
  never wrap it with `asyncio.run()`.
- Before file relay, discover the schemas and verify the destination natively.
  Pass the opaque payload between the known tools without exposing it to the model.
- A failed PTC snippet is a completed failed tool call, not a request to keep probing
  the sandbox. Choose corrected code or a native call based on the actual error.
  After identifying the cause, reuse available REPL values and resume from the
  failed step. Check already-started calls before retrying. A script error
  does not roll back earlier nested side effects; never blindly replay an uncertain write.
- Use `load_file_data` only inside `run_code` when an authorized session file or a
  public HTTP(S) file must be passed unchanged to an MCP string argument. Provide
  exactly one of `file_id` and `url`. Treat the returned value as a mapping,
  access fields with `payload['name']`, and pass `payload['content']` unchanged to the
  next tool. Never inspect, measure, print, or return the `content` value.
- Return only what the task needs. For diagnostics prefer counts to serializing
  intermediate data. Do not print file payloads, Base64, or large values.
- For HTML/text pages, web_fetch returns an object with url, title and content.
  Extract relevant lines or links from its Markdown here without refetching.
  For visual inspection, return the File ID or URL and call outer view_image.
- Authorized MCP, web_search, web_fetch, execute_python, execute_skill_script and
  export_file_to_download_center retain their ordinary native entry points. Only
  functions actually listed in this catalog can also be used here. Call Python
  with code and JSON inputs; call a Skill with its exact authorized script path.
  No save_file_data, read_file, parse_document, UI confirmation or Shell function
  is added to this catalog. Python executions still have separate temporary dirs.
- When Python/Skill needs the current user's identity/app scope, pass
  include_context=True for credential-free DEEP_AGENT_CONTEXT JSON; otherwise leave
  False. Native and nested calls share the Run snapshot. Monty cannot read it.
- In a trusted channel run, channel_message is also available natively and here.
  Forward a known public media URL directly with components=[{'type': 'image',
  'url': public_url}], or use an authorized file_id. No Python or platform upload
  is needed for unchanged media. Do not retry a send with an unknown outcome;
  the PTC deadline may expire after gateway dispatch, without undoing delivery.
- Internal returns go to the program without model-display pagination, up to
  32 MiB per return. Tool/source limits and incomplete-result markers still apply.
  Python/Skill inputs allow 16 MiB JSON. Monty memory is 256 MiB, each snippet's
  execution allowance is 120 seconds, and each run_code also has a 120-second total
  deadline including tool waits and at most 100 calls. Reduce/aggregate over-limit
  data; do not assume the platform automatically pages the source or saves it.
- A Skill may return complete HTML/text for export_file_to_download_center with
  source.type='content'. A Python-generated authorized file may use 'platform_file'.
  Publish final deliverables only; return their genuine receipt, not the document.
- Execute side-effecting tools sequentially. After an uncertain failure, inspect the
  target state instead of blindly repeating the write. Follow each tool's native
  schema and the selected Skill for service-specific prerequisites and workflows;
  generic examples below are placeholders, not a fixed business recipe.
- `run_code` cannot directly access host files, environment variables, network sockets,
  Shell, pip, or third-party packages. Only listed catalog functions are callable.

Generic numeric summary shape (example tool returns complete rows or raises an
error; use the actual catalog alias, fields and status checks for your tool):
```python
result = await fetch_rows()
rows = result.get("rows") if isinstance(result, dict) else None
if not isinstance(rows, list):
    raise ValueError("Expected a rows list")
total = 0.0
count = 0
for row in rows:
    value = row.get("value") if isinstance(row, dict) else None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        total = total + float(value)
        count = count + 1
{"row_count": len(rows), "numeric_count": count, "total": total if count else None}
```

Generic authorized-file relay shape (for a public file, pass `url` instead of `file_id`):
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


def _monty_compatibility_guidance(error: BaseException) -> str:
    # Match the interpreter's exception, never a tool result or captured stdout.
    # Suggestions do not rewrite code, retry tools, or reset the shared REPL.
    if not isinstance(error, ModelRetry) or not isinstance(error.__cause__, MontyRuntimeError):
        return ""
    last_line = error.__cause__.display().strip().rsplit("\n", 1)[-1]
    if last_line == "TypeError: JSONEncoder.__init__() got an unexpected keyword argument 'cls'":
        return (
            " Monty's JSON encoder does not accept cls. Use json.dumps(value, default=...) "
            "for explicit fallback conversion, or an authorized Python/Skill tool "
            "when a custom encoder class is required."
        )
    if re.fullmatch(
        r"TypeError: JSONDecoder\.__init__\(\) got an unexpected keyword argument "
        r"'(?:object_hook|object_pairs_hook|parse_constant|cls)'",
        last_line,
    ):
        return (
            " This JSON decoder hook is unsupported. Parse with json.loads(text) and "
            "transform explicitly, or use an authorized Python/Skill tool "
            "when custom decoding is required."
        )
    if last_line == "NotImplementedError: gather() does not yet support keyword arguments":
        return (
            " Use await asyncio.gather(a(), b()) without keyword arguments. "
            "return_exceptions=True is unsupported; handle errors explicitly inside each "
            "read-only coroutine if needed. Check completed calls before continuing."
        )
    if last_line == "NotImplementedError: The monty syntax parser does not yet support yield expressions":
        return (
            " Monty has no yield generators. Use bounded ordinary loops or list comprehensions; "
            "generator expressions eagerly produce lists, not lazy iterators."
        )
    return ""


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
            # Harness 0.32's prose embeds an old Monty module allowlist and says
            # supported APIs such as asyncio.sleep are unavailable. Own the prose,
            # while preserving the SDK's exact authorized callable definitions.
            description = "\n".join((
                _ROUTING_GUIDANCE, _EXECUTION_GUIDANCE,
                self._render_catalog(run_code.callable_defs), _USAGE_RULES,
            ))
            result[_RUN_CODE_NAME] = replace(
                run_code,
                tool_def=replace(
                    run_code.tool_def,
                    description=description,
                ),
            )
        return result

    @staticmethod
    def _render_catalog(callable_defs: dict[str, Any]) -> str:
        if not callable_defs:
            return ""
        rows = [
            "Functions available inside `run_code`. Call forms show `await` only "
            "for async functions; call synchronous functions directly. Read each "
            "ordinary tool schema for parameters and semantics:"
        ]
        for alias, tool_def in sorted(callable_defs.items()):
            metadata = tool_def.metadata or {}
            original = metadata.get(CODE_MODE_ORIGINAL_NAME_KEY)
            call_prefix = "" if getattr(tool_def, "sequential", False) else "await "
            if alias == "load_file_data":
                rows.append(
                    f"- `{call_prefix}load_file_data(file_id: str | None = None, url: str | None "
                    "= None, encoding: Literal['base64', 'text'] = 'base64', "
                    "text_encoding: str = 'utf-8', max_chars: int = 65536) -> FileData` "
                    "(code-only authorized session/public-file primitive; provide exactly "
                    "one source and access "
                    "the returned mapping with `payload['name']` / `payload['content']`)"
                )
            elif isinstance(original, str) and original != alias:
                rows.append(f"- `{original}` -> Python `{call_prefix}{alias}(...)`")
            else:
                rows.append(f"- `{call_prefix}{alias}(...)`")
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
            guidance = (
                "PTC execution failed. Inspect the error and completed calls before continuing. "
                "Correct read-only code only after identifying the cause; "
                "search_tools and schema discovery must stay native. "
                "Earlier nested writes may have succeeded: inspect target state "
                "before retrying; do not repeat the same failed snippet or switch "
                "paths to repeat an uncertain write."
            )
            guidance += _monty_compatibility_guidance(exc)
            if collector.execution is not None and any(
                call.tool_name == "channel_message" for call in collector.execution.nested_calls.values()
            ):
                guidance += (" If channel delivery reached the gateway, its outcome may be unknown. "
                             "Do not resend through either native tools or PTC without confirming delivery.")
            # Preserve a real failed ToolReturnPart (including bounded audit) rather
            # than asking the model to retry code until the whole Agent run aborts.
            # This is the framework's native failure signal, not a successful result
            # with an application-only error flag. Cancellation/deferral is untouched.
            raise ToolFailedError(ToolReturnPart(
                tool_name=name,
                tool_call_id=ctx.tool_call_id or "",
                content={
                    "error": safe_audit_value(safe_message, sensitive_values=secrets),
                    "guidance": guidance,
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
