from __future__ import annotations

import asyncio
import hashlib
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from pydantic_ai import RunContext
from pydantic_ai.messages import ModelMessage, ModelRequest, ToolReturnPart
from pydantic_ai.tools import ToolDefinition
from pydantic_ai_harness.tool_output_limits import (
    Band,
    OverflowStore,
    Spill,
    ToolOutputLimits,
    Truncate,
)

from deep_agent.agent.attachments import _download_file
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.observability import log_event
from deepfos_platform.client.platform import FILE_TYPE_ATTACHMENT, PlatformAPI

TOOL_OUTPUT_MIN_TOKENS = 4_000
TOOL_OUTPUT_MAX_TOKENS = 24_000
TOOL_OUTPUT_DEFAULT_TOKENS = 24_000
TOOL_OUTPUT_RATIO = 0.20
TOOL_OUTPUT_PREVIEW_CHARS = 2_000
TOOL_OUTPUT_FALLBACK_CHARS = 8_000

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


def tool_result_artifacts(part: ToolReturnPart) -> list[dict[str, Any]]:
    """Expose Harness spill handles without leaking other tool metadata."""
    metadata = _metadata_dict(part)
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
        artifacts.append(item)
    content_handle = metadata.get("overflow_content_handle")
    if isinstance(content_handle, str) and content_handle:
        artifacts.append(
            {
                "artifact_id": content_handle,
                "kind": "tool_result_overflow",
                "role": "content",
            }
        )
    return artifacts


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
    ) -> None:
        self._headers = dict(request_headers)
        self._deps: AgentDeps | None = None
        self._allowed_handles: set[str] = set()
        self._load_tasks: dict[str, asyncio.Task[bytes]] = {}
        self.telemetry = ToolOutputTelemetry(threshold_tokens=threshold_tokens)

    def bind(self, deps: AgentDeps, raw_history: Sequence[ModelMessage]) -> None:
        self._deps = deps
        artifacts = artifact_metadata_from_history(raw_history)
        self._allowed_handles = set(artifacts)
        deps.tool_output_artifacts = artifacts
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
        api = PlatformAPI(header=self._headers)
        try:
            uploaded = await api.file.upload(
                file_type=FILE_TYPE_ATTACHMENT,
                file_name=f"tool-result-{digest}.txt",
                file=data,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            self.telemetry.spill_failures += 1
            log_event(
                "tool_output_spill_failed",
                **self._log_fields(),
                spill_failures=self.telemetry.spill_failures,
            )
            raise
        raw_handle = getattr(uploaded, "id", None)
        if not isinstance(raw_handle, str) or not raw_handle:
            self.telemetry.spill_failures += 1
            raise OSError("Platform file upload returned no id")
        handle = raw_handle
        self._allowed_handles.add(handle)
        if self._deps is not None:
            self._deps.file_bytes[self._cache_key(handle)] = data
            self._deps.tool_output_artifacts[handle] = {
                "artifact_id": handle,
                "kind": "tool_result_overflow",
                "role": "return_value",
                "byte_size": len(data),
            }
        self.telemetry.spill_count += 1
        self.telemetry.spilled_bytes += len(data)
        log_event(
            "tool_output_spilled",
            **self._log_fields(),
            spill_count=self.telemetry.spill_count,
            spilled_bytes=len(data),
        )
        return handle

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
                    data = await _download_file(handle, self._headers)
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


def build_tool_output_limits(
    *,
    request_headers: Mapping[str, str],
    context_window_tokens: int | None,
    max_output_tokens: int | None,
    estimator: TokenEstimator,
) -> tuple[ToolOutputLimits[AgentDeps], PlatformToolResultStore]:
    threshold = tool_output_threshold_tokens(
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
    )
    store = PlatformToolResultStore(
        request_headers=request_headers,
        threshold_tokens=threshold,
    )
    capability: ToolOutputLimits[AgentDeps] = ToolOutputLimits(
        bands=[
            Band(
                over=threshold,
                action=Spill(
                    preview_chars=TOOL_OUTPUT_PREVIEW_CHARS,
                    then=Truncate(max_chars=TOOL_OUTPUT_FALLBACK_CHARS),
                ),
            )
        ],
        tool_filter=_tool_output_selected,
        over_tokens=True,
        tokenizer=estimator.text,
        store=store,
    )
    return capability, store


__all__ = [
    "PlatformToolResultStore",
    "ToolOutputTelemetry",
    "artifact_handles_from_history",
    "artifact_metadata_from_history",
    "build_tool_output_limits",
    "tool_output_threshold_tokens",
    "tool_result_artifacts",
]
