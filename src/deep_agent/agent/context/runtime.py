from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from collections.abc import Awaitable, Callable
from typing import Any

from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.context.budget import OutputTokenSample
from deep_agent.agent.context.estimator import TokenEstimator


@dataclass
class CompactionTelemetry:
    triggered: bool = False
    trigger_count: int = 0
    strategy: str = "none"
    target_met: bool = False
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0
    max_estimated_tokens_before: int = 0
    messages_before: int = 0
    messages_after: int = 0
    reduction_ratio: float = 0.0
    summary_applied: bool = False
    summary_failed: bool = False
    summary_calls: int = 0
    summary_chunks: int = 0
    fallback_reason: str | None = None
    failure: str | None = None
    target_tokens: int | None = None
    candidate_tokens: int | None = None
    retained_tail_tokens: int | None = None
    evidence_tokens: int | None = None
    summary_tokens: int | None = None
    estimated_input_tokens: int | None = None
    provider_input_tokens: int | None = None
    estimation_error_ratio: float | None = None
    max_absolute_estimation_error_ratio: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "trigger_count": self.trigger_count,
            "strategy": self.strategy,
            "target_met": self.target_met,
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "max_estimated_tokens_before": self.max_estimated_tokens_before,
            "messages_before": self.messages_before,
            "messages_after": self.messages_after,
            "reduction_ratio": self.reduction_ratio,
            "summary_applied": self.summary_applied,
            "summary_failed": self.summary_failed,
            "summary_calls": self.summary_calls,
            "summary_chunks": self.summary_chunks,
            "fallback_reason": self.fallback_reason,
            "failure": self.failure,
            "target_tokens": self.target_tokens,
            "candidate_tokens": self.candidate_tokens,
            "retained_tail_tokens": self.retained_tail_tokens,
            "evidence_tokens": self.evidence_tokens,
            "summary_tokens": self.summary_tokens,
            "estimated_input_tokens": self.estimated_input_tokens,
            "provider_input_tokens": self.provider_input_tokens,
            "estimation_error_ratio": self.estimation_error_ratio,
            "max_absolute_estimation_error_ratio": self.max_absolute_estimation_error_ratio,
        }


@dataclass
class ContextRunState:
    raw_history: list[ModelMessage]
    estimator: TokenEstimator
    usage_limits: UsageLimits | None = None
    event_queue: asyncio.Queue[dict[str, Any]] | None = None
    output_samples: list[OutputTokenSample] = field(default_factory=list)
    telemetry: CompactionTelemetry = field(default_factory=CompactionTelemetry)
    last_budget: Any | None = None
    request_index: int = 0
    pending_request_estimate: int | None = None
    compaction_event_id: str | None = None
    compaction_started_at: float | None = None
    summary_call: dict[str, Any] | None = None
    compaction_request_parameters: Any | None = field(default=None, repr=False)
    compaction_estimated_tokens_before: int | None = None
    compaction_target_tokens: int | None = None
    compaction_estimate_scope: str = "history_only"
    pruned_tool_results: list[dict[str, Any]] = field(default_factory=list)
    start_compaction_event: Callable[[str], Awaitable[Any]] | None = field(
        default=None, repr=False
    )
    finish_compaction_event: Callable[[str, str | None], Awaitable[Any]] | None = field(
        default=None, repr=False
    )

    async def begin_compaction(self, trigger: str) -> None:
        self.compaction_started_at = time.perf_counter()
        self.summary_call = None
        self.pruned_tool_results = []
        if self.start_compaction_event is None:
            return
        event = await self.start_compaction_event(trigger)
        self.compaction_event_id = getattr(event, "event_id", None)
        if self.event_queue is not None and event is not None:
            self.event_queue.put_nowait(
                {
                    "type": "session_event_appended",
                    "persisted": True,
                    "event": event.public_dict(),
                }
            )

    async def finish_compaction(self, status: str, error: str | None = None) -> None:
        if self.finish_compaction_event is None or self.compaction_event_id is None:
            return
        event = await self.finish_compaction_event(status, error)
        if self.event_queue is not None and event is not None:
            self.event_queue.put_nowait(
                {
                    "type": "session_event_updated",
                    "persisted": True,
                    "event": event.public_dict(),
                }
            )

    def emit(self, phase: str, message: str, **fields: Any) -> None:
        if self.event_queue is None:
            return
        payload = {
            "type": "context_compaction_progress",
            "persisted": False,
            "event_id": self.compaction_event_id,
            "phase": phase,
            "message": message,
            "request_index": self.request_index,
            **fields,
        }
        self.event_queue.put_nowait(payload)
