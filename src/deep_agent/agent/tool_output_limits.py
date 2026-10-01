from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass
from typing import Annotated, Any, Mapping, Sequence

from pydantic import Field
from pydantic_ai import FunctionToolset, RunContext, ToolReturn
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.tools import ToolDefinition
from pydantic_ai_harness.tool_output_limits import (
    Band,
    OverflowStore,
    Spill,
    ToolOutputLimits,
    Truncate,
    json_lines,
)

from deep_agent.agent.attachments import _download_file
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.observability import log_event
from deep_agent.files.storage import FileStore, PlatformFileStore
from deep_agent.files.reading_budget import ReadingBudget
from deep_agent.files.tool_result_reader import read_stored_tool_result
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT

TOOL_OUTPUT_MIN_TOKENS = 4_000
# Long document tools may return ~30K CJK characters. Allow those inline only
# when the configured model input budget supports it; unknown windows stay 24K.
TOOL_OUTPUT_MAX_TOKENS = 48_000
TOOL_OUTPUT_DEFAULT_TOKENS = 24_000
TOOL_OUTPUT_RATIO = 0.20
TOOL_OUTPUT_PREVIEW_CHARS = 2_000
TOOL_OUTPUT_FALLBACK_CHARS = 8_000
TOOL_OUTPUT_FALLBACK_TAIL_LINES = 5

_EXCLUDED_TOOLS = frozenset(
    {
        "load_capability",
        "read_file",
        "read_tool_result",
        "search_tools",
        "tool_search",
    }
)


@dataclass
class ToolOutputTelemetry:
    threshold_tokens: int
    spill_count: int = 0
    spilled_bytes: int = 0
    read_calls: int = 0
    platform_downloads: int = 0
    spill_failures: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "threshold_tokens": self.threshold_tokens,
            "spill_count": self.spill_count,
            "spilled_bytes": self.spilled_bytes,
            "read_calls": self.read_calls,
            "platform_downloads": self.platform_downloads,
            "spill_failures": self.spill_failures,
        }


def tool_output_threshold_tokens(
    *,
    context_window_tokens: int | None,
    max_output_tokens: int | None,
) -> int:
    """Return the per-tool spill threshold without adding runtime configuration."""
    if context_window_tokens is None or context_window_tokens <= 0:
        return TOOL_OUTPUT_DEFAULT_TOKENS
    available_input = max(1, context_window_tokens - max(0, max_output_tokens or 0))
    return max(
        TOOL_OUTPUT_MIN_TOKENS,
        min(TOOL_OUTPUT_MAX_TOKENS, round(available_input * TOOL_OUTPUT_RATIO)),
    )


def _tool_output_selected(
    _ctx: RunContext[AgentDeps],
    tool_def: ToolDefinition,
) -> bool:
    return tool_def.name not in _EXCLUDED_TOOLS


def _metadata_dict(part: ToolReturnPart) -> Mapping[str, Any]:
    metadata = part.metadata
    return metadata if isinstance(metadata, Mapping) else {}


def _generated_file_refs(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_files = metadata.get("generated_files")
    if not isinstance(raw_files, Sequence) or isinstance(raw_files, (str, bytes)):
        return []
    artifacts: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw_file in raw_files:
        if not isinstance(raw_file, Mapping):
            continue
        file_id = raw_file.get("file_id")
        if not isinstance(file_id, str) or not file_id or file_id in seen:
            continue
        seen.add(file_id)
        item: dict[str, Any] = {
            "file_id": file_id,
            "kind": "sandbox_output",
        }
        file_name = raw_file.get("file_name")
        if isinstance(file_name, str) and file_name:
            item["file_name"] = file_name
        mime = raw_file.get("mime")
        if isinstance(mime, str) and mime:
            item["mime"] = mime
        byte_size = raw_file.get("byte_size")
        if isinstance(byte_size, int) and byte_size >= 0:
            item["byte_size"] = byte_size
        sha256 = raw_file.get("sha256")
        if isinstance(sha256, str) and sha256:
            item["sha256"] = sha256
        artifacts.append(item)
    return artifacts


def _generated_file_artifacts(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for file_ref in _generated_file_refs(metadata):
        item = {
            **file_ref,
            "artifact_id": file_ref["file_id"],
            "role": "generated_file",
        }
        if file_ref.get("sha256"):
            item["content_hash"] = file_ref["sha256"]
        artifacts.append(item)
    return artifacts


def artifacts_from_metadata(metadata: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Project safe FileStore references from application-only tool metadata."""
    artifacts: list[dict[str, Any]] = []
    value_handle = metadata.get("overflow_handle")
    if isinstance(value_handle, str) and value_handle:
        item: dict[str, Any] = {
            "artifact_id": value_handle,
            "kind": "tool_result_overflow",
            "role": "return_value",
        }
        byte_size = metadata.get("overflow_bytes")
        if isinstance(byte_size, int) and byte_size >= 0:
            item["byte_size"] = byte_size
        content_hash = metadata.get("overflow_hash")
        if isinstance(content_hash, str) and content_hash:
            item["content_hash"] = content_hash
        artifacts.append(item)
    content_handle = metadata.get("overflow_content_handle")
    if isinstance(content_handle, str) and content_handle:
        item = {
            "artifact_id": content_handle,
            "kind": "tool_result_overflow",
            "role": "content",
        }
        content_hash = metadata.get("overflow_content_hash")
        if isinstance(content_hash, str) and content_hash:
            item["content_hash"] = content_hash
        artifacts.append(item)
    artifacts.extend(_generated_file_artifacts(metadata))
    return artifacts


def tool_result_artifacts(part: ToolReturnPart) -> list[dict[str, Any]]:
    """Expose safe file references without leaking bytes or temporary paths."""
    return artifacts_from_metadata(_metadata_dict(part))


def artifact_handles_from_history(messages: Sequence[ModelMessage]) -> set[str]:
    """Recover only authenticated spill handles from the raw session history."""
    return set(artifact_metadata_from_history(messages))


def artifact_metadata_from_history(
    messages: Sequence[ModelMessage],
) -> dict[str, dict[str, Any]]:
    """Recover authenticated spill metadata needed by other runtime file tools."""
    artifacts: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            for artifact in artifacts_from_metadata(_metadata_dict(part)):
                if artifact.get("kind") != "tool_result_overflow":
                    continue
                artifact_id = artifact.get("artifact_id")
                if isinstance(artifact_id, str) and artifact_id:
                    artifacts[artifact_id] = dict(artifact)
    return artifacts


def session_file_metadata_from_history(
    messages: Sequence[ModelMessage],
) -> dict[str, dict[str, Any]]:
    """Recover FileStore authorization only from raw persisted tool results."""
    artifacts: dict[str, dict[str, Any]] = {}
    for message in messages:
        if not isinstance(message, ModelRequest):
            continue
        for part in message.parts:
            if not isinstance(part, ToolReturnPart):
                continue
            for artifact in tool_result_artifacts(part):
                artifact_id = artifact.get("artifact_id")
                if isinstance(artifact_id, str) and artifact_id:
                    artifacts[artifact_id] = dict(artifact)
    return artifacts


class PlatformToolResultStore(OverflowStore):
    """Request-scoped Harness store backed by DeepFOS platform files."""

    def __init__(
        self,
        *,
        request_headers: Mapping[str, str],
        threshold_tokens: int,
        file_store: FileStore | None = None,
    ) -> None:
        self._headers = dict(request_headers)
        self._file_store = file_store or PlatformFileStore()
        self._deps: AgentDeps | None = None
        self._allowed_handles: set[str] = set()
        self._load_tasks: dict[str, asyncio.Task[bytes]] = {}
        self._content_hashes: dict[str, str] = {}
        self._failed_inline_payloads: list[bytes] = []
        self.telemetry = ToolOutputTelemetry(threshold_tokens=threshold_tokens)

    def bind(self, deps: AgentDeps, raw_history: Sequence[ModelMessage]) -> None:
        self._deps = deps
        artifacts = artifact_metadata_from_history(raw_history)
        session_artifacts = session_file_metadata_from_history(raw_history)
        self._allowed_handles = set(artifacts)
        self._content_hashes = {
            handle: str(metadata["content_hash"])
            for handle, metadata in artifacts.items()
            if metadata.get("content_hash")
        }
        deps.tool_output_artifacts = artifacts
        deps.session_file_artifacts = session_artifacts
        deps.tool_output_limits = self.telemetry

    @staticmethod
    def _cache_key(handle: str) -> str:
        return f"tool-result:{handle}"

    def _log_fields(self) -> dict[str, Any]:
        deps = self._deps
        if deps is None:
            return {}
        return {
            "conversation_id": deps.conversation_id,
            "run_id": deps.run_id,
            "agent_id": deps.agent_id,
            "session_id": deps.session_id,
        }

    async def write(self, key: str, data: bytes) -> str:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:20]
        try:
            uploaded = await self._file_store.upload(
                file_type=FILE_TYPE_ATTACHMENT,
                file_name=f"tool-result-{digest}.txt",
                mime="text/plain",
                data=data,
                request_headers=self._headers,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self._failed_inline_payloads.append(data)
            self.telemetry.spill_failures += 1
            log_event(
                "tool_output_spill_failed",
                **self._log_fields(),
                spill_failures=self.telemetry.spill_failures,
            )
            raise
        raw_handle = uploaded.id
        if not isinstance(raw_handle, str) or not raw_handle:
            self._failed_inline_payloads.append(data)
            self.telemetry.spill_failures += 1
            raise OSError("Platform file upload returned no id")
        handle = raw_handle
        self._allowed_handles.add(handle)
        content_hash = hashlib.sha256(data).hexdigest()
        self._content_hashes[handle] = content_hash
        if self._deps is not None:
            self._deps.file_bytes[self._cache_key(handle)] = data
            self._deps.tool_output_artifacts[handle] = {
                "artifact_id": handle,
                "kind": "tool_result_overflow",
                "role": "return_value",
                "byte_size": len(data),
                "content_hash": content_hash,
            }
            self._deps.session_file_artifacts[handle] = dict(
                self._deps.tool_output_artifacts[handle]
            )
        self.telemetry.spill_count += 1
        self.telemetry.spilled_bytes += len(data)
        log_event(
            "tool_output_spilled",
            **self._log_fields(),
            spill_count=self.telemetry.spill_count,
            spilled_bytes=len(data),
        )
        return handle

    def consume_failed_inline_payloads(self) -> list[bytes]:
        payloads = self._failed_inline_payloads
        self._failed_inline_payloads = []
        return payloads

    def content_hash(self, handle: str) -> str | None:
        return self._content_hashes.get(handle)

    async def read(self, handle: str) -> bytes:
        self.telemetry.read_calls += 1
        if handle not in self._allowed_handles:
            raise FileNotFoundError("Unknown tool result artifact")
        deps = self._deps
        if deps is None:
            raise FileNotFoundError("Tool result store is not bound to a run")
        cache_key = self._cache_key(handle)
        cached = deps.file_bytes.get(cache_key)
        if cached is not None:
            return cached
        task = self._load_tasks.get(handle)
        if task is None:
            async def download_and_cache() -> bytes:
                try:
                    data = await _download_file(
                        handle,
                        self._headers,
                        file_store=self._file_store,
                    )
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    raise OSError("Tool result artifact download failed") from exc
                if not isinstance(data, bytes):
                    raise OSError("Tool result artifact download returned non-bytes")
                deps.file_bytes[cache_key] = data
                self.telemetry.platform_downloads += 1
                return data

            task = asyncio.create_task(
                download_and_cache(),
                name="tool-result-download",
            )
            self._load_tasks[handle] = task
        try:
            data = await task
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            raise OSError("Tool result artifact download failed") from exc
        finally:
            self._load_tasks.pop(handle, None)
        return data


class DeepAgentToolOutputLimits(ToolOutputLimits[AgentDeps]):
    """Preserve a complete Event-only fallback when platform spill fails."""

    def __init__(self, *, reading_budget: ReadingBudget, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.reading_budget = reading_budget

    def get_toolset(self) -> FunctionToolset[AgentDeps]:
        # Use the public capability extension point, not SDK globals/private readers.
        store = self.store
        assert store is not None

        async def read_tool_result(
            ctx: RunContext[AgentDeps],
            handle: str,
            offset: int = 0,
            limit: int | None = None,
            from_end: bool = False,
            pattern: str | None = None,
            char_offset: int = 0,
            end_offset: int | None = None,
            context_lines: Annotated[int, Field(ge=0, le=5, strict=True)] = 0,
        ) -> dict[str, Any]:
            """Read an authorized spilled result within a 24000/48000 Token budget, including its envelope.

            Args:
                handle: Exact overflow handle from the original tool result.
                offset: Zero-based count of selected lines to skip, not a source line number.
                limit: Maximum source lines; omit to read as much as the Token budget allows.
                from_end: Select a tail window, retaining document order.
                pattern: Literal substring filter, not a regular expression.
                char_offset: Zero-based character position within the first selected line.
                end_offset: Exclusive forward boundary, copied from next_read only.
                context_lines: Surrounding lines per match (0-5, default 0); try 3 to see ownership.

            Call with the returned next_read arguments to continue without losing a
            partial line. For tail reads, finish next_read first; previous_read selects
            an older window. Overlapping context is merged before pagination.
            source_line_numbers gives original one-based positions for each returned
            content line; source_total_count is the original document's line count.
            Changing pattern or context_lines starts a new pagination sequence.
            """
            return await read_stored_tool_result(
                store, handle, offset=offset, limit=limit, from_end=from_end,
                pattern=pattern, char_offset=char_offset, end_offset=end_offset,
                context_lines=context_lines, budget=self.reading_budget,
            )

        return FunctionToolset([read_tool_result])

    async def after_tool_execute(
        self,
        ctx: RunContext[AgentDeps],
        *,
        call: ToolCallPart,
        tool_def: ToolDefinition,
        args: dict[str, Any],
        result: Any,
    ) -> Any:
        reduced = await super().after_tool_execute(
            ctx,
            call=call,
            tool_def=tool_def,
            args=args,
            result=result,
        )
        if isinstance(result, ToolReturn) and isinstance(reduced, ToolReturn):
            original_metadata = (
                result.metadata if isinstance(result.metadata, Mapping) else {}
            )
            generated_files = _generated_file_refs(original_metadata)
            reduced_metadata = (
                dict(reduced.metadata) if isinstance(reduced.metadata, Mapping) else {}
            )
            if generated_files:
                reduced_metadata["generated_files"] = generated_files
                return_value = reduced.return_value
                if isinstance(return_value, Mapping):
                    return_value = dict(return_value)
                    return_value["files"] = generated_files
                elif not (
                    isinstance(return_value, list)
                    and all(item in return_value for item in generated_files)
                ):
                    return_value = {
                        "result": return_value,
                        "files": generated_files,
                    }
                reduced = ToolReturn(
                    return_value=return_value,
                    content=reduced.content,
                    metadata=reduced_metadata,
                )
        store = self.store
        if not isinstance(store, PlatformToolResultStore):
            return reduced
        if isinstance(reduced, ToolReturn):
            metadata = dict(reduced.metadata) if isinstance(reduced.metadata, Mapping) else {}
            value_handle = metadata.get("overflow_handle")
            content_handle = metadata.get("overflow_content_handle")
            value_hash = (
                store.content_hash(value_handle) if isinstance(value_handle, str) else None
            )
            content_hash = (
                store.content_hash(content_handle)
                if isinstance(content_handle, str)
                else None
            )
            if value_hash or content_hash:
                if value_hash:
                    metadata["overflow_hash"] = value_hash
                if content_hash:
                    metadata["overflow_content_hash"] = content_hash
                reduced = ToolReturn(
                    return_value=reduced.return_value,
                    content=reduced.content,
                    metadata=metadata,
                )
        failed_payloads = store.consume_failed_inline_payloads()
        if not failed_payloads:
            return reduced

        if isinstance(result, ToolReturn):
            original_value = result.return_value
            original_content = result.content
        else:
            original_value = result
            original_content = None
        inline_fallback = _json_safe_value(original_value)
        canonical = json.dumps(
            inline_fallback,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if isinstance(reduced, ToolReturn):
            metadata = dict(reduced.metadata) if isinstance(reduced.metadata, Mapping) else {}
            return_value = reduced.return_value
            content = reduced.content
        else:
            metadata = {}
            return_value = reduced
            content = original_content
        metadata.update(
            {
                "spill_failed": True,
                "inline_fallback": inline_fallback,
                "inline_fallback_bytes": len(canonical),
                "inline_fallback_hash": hashlib.sha256(canonical).hexdigest(),
                "failed_spill_units": len(failed_payloads),
            }
        )
        return ToolReturn(
            return_value=return_value,
            content=content,
            metadata=metadata,
        )


def _json_safe_value(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, bytes):
        return {
            "encoding": "base64",
            "data": base64.b64encode(value).decode("ascii"),
        }
    if isinstance(value, Mapping):
        return {str(key): _json_safe_value(child) for key, child in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe_value(child) for child in value]
    if hasattr(value, "model_dump"):
        return _json_safe_value(value.model_dump(mode="json"))
    return str(value)


def build_tool_output_limits(
    *,
    request_headers: Mapping[str, str],
    context_window_tokens: int | None,
    max_output_tokens: int | None,
    estimator: TokenEstimator,
    file_store: FileStore | None = None,
) -> tuple[ToolOutputLimits[AgentDeps], PlatformToolResultStore]:
    threshold = tool_output_threshold_tokens(
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
    )
    store = PlatformToolResultStore(
        request_headers=request_headers,
        threshold_tokens=threshold,
        file_store=file_store,
    )
    capability = DeepAgentToolOutputLimits(
        reading_budget=ReadingBudget(estimator, context_window_tokens),
        bands=[
            Band(
                over=threshold,
                action=Spill(
                    preview_chars=TOOL_OUTPUT_PREVIEW_CHARS,
                    then=Truncate(
                        max_chars=TOOL_OUTPUT_FALLBACK_CHARS,
                        keep_tail_lines=TOOL_OUTPUT_FALLBACK_TAIL_LINES,
                    ),
                ),
            )
        ],
        tool_filter=_tool_output_selected,
        over_tokens=True,
        tokenizer=estimator.text,
        store=store,
        serializer=json_lines,
    )
    return capability, store


__all__ = [
    "PlatformToolResultStore",
    "ToolOutputTelemetry",
    "artifact_handles_from_history",
    "artifact_metadata_from_history",
    "artifacts_from_metadata",
    "build_tool_output_limits",
    "session_file_metadata_from_history",
    "tool_output_threshold_tokens",
    "tool_result_artifacts",
]
