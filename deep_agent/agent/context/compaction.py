from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelMessage, ModelRequest, SystemPromptPart
from pydantic_ai.tools import RunContext
from pydantic_ai_harness.compaction import (
    ClampOversizedMessages,
    ClearToolResults,
    DeduplicateFileReads,
    SlidingWindow,
    SummarizingCompaction,
    TieredCompaction,
    estimate_token_count,
)

from deep_agent.agent.context.budget import ContextBudget, build_context_budget
from deep_agent.agent.context.file_keys import deep_agent_file_key
from deep_agent.options import OPTION

if TYPE_CHECKING:
    from pydantic_ai.models import Model, ModelRequestContext

_SUMMARY_PREFIX = "Summary of previous conversation:\n\n"


def _summaries(messages: list[ModelMessage]) -> tuple[str, ...]:
    return tuple(
        part.content
        for message in messages
        if isinstance(message, ModelRequest)
        for part in message.parts
        if isinstance(part, SystemPromptPart)
        and part.content.startswith(_SUMMARY_PREFIX)
    )


@dataclass
class CompactionTelemetry:
    triggered: bool = False
    strategy: str = "none"
    estimated_tokens_before: int = 0
    estimated_tokens_after: int = 0
    messages_before: int = 0
    messages_after: int = 0
    summary_applied: bool = False
    summary_failed: bool = False
    failure: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "triggered": self.triggered,
            "strategy": self.strategy,
            "estimated_tokens_before": self.estimated_tokens_before,
            "estimated_tokens_after": self.estimated_tokens_after,
            "messages_before": self.messages_before,
            "messages_after": self.messages_after,
            "summary_applied": self.summary_applied,
            "summary_failed": self.summary_failed,
            "failure": self.failure,
        }


@dataclass
class DeepAgentContextCompaction(AbstractCapability[Any]):
    """Budget-aware wrapper that drives Harness' tiered compaction."""

    context_window_tokens: int
    max_output_tokens: int | None
    summary_model: "str | Model | None"
    summary_timeout: float
    telemetry: CompactionTelemetry = field(default_factory=CompactionTelemetry, init=False)
    last_budget: ContextBudget | None = field(default=None, init=False)

    async def before_run(self, ctx: RunContext[Any]) -> None:
        # Agent/Capability instances may be reused between requests.  Reset once
        # per run, while still accumulating telemetry across multi-step tool runs.
        self.telemetry = CompactionTelemetry()
        self.last_budget = None

    async def before_model_request(
        self,
        ctx: RunContext[Any],
        request_context: "ModelRequestContext",
    ) -> "ModelRequestContext":
        messages = list(request_context.messages)
        summaries_before = _summaries(messages)
        budget = build_context_budget(
            messages,
            context_window_tokens=self.context_window_tokens,
            max_output_tokens=self.max_output_tokens,
        )
        self.last_budget = budget
        before_tokens = estimate_token_count(messages)
        self.telemetry.estimated_tokens_before = max(
            self.telemetry.estimated_tokens_before,
            before_tokens,
        )
        self.telemetry.estimated_tokens_after = before_tokens
        self.telemetry.messages_before = max(
            self.telemetry.messages_before,
            len(messages),
        )
        self.telemetry.messages_after = len(messages)

        trigger = budget.trigger_tokens
        if not budget.enabled or trigger is None or before_tokens <= trigger:
            return request_context

        self.telemetry.triggered = True
        tiered, fallback = _build_harness_strategies(
            budget,
            summary_model=self.summary_model,
        )
        try:
            async with asyncio.timeout(self.summary_timeout):
                compacted = await tiered.compact(messages, ctx)
        except Exception as exc:  # summary/provider failure must not overwrite a good checkpoint
            self.telemetry.summary_failed = True
            self.telemetry.failure = type(exc).__name__
            compacted = await fallback.compact(messages, ctx)

        request_context.messages = compacted
        self.telemetry.estimated_tokens_after = estimate_token_count(compacted)
        self.telemetry.messages_after = len(compacted)
        summaries_after = _summaries(compacted)
        self.telemetry.summary_applied = self.telemetry.summary_applied or (
            bool(summaries_after) and summaries_after != summaries_before
        )
        if self.telemetry.summary_applied:
            self.telemetry.strategy = "tiered_summary"
        elif self.telemetry.summary_failed:
            self.telemetry.strategy = "sliding_window_fallback"
        else:
            self.telemetry.strategy = "tiered_deterministic"
        return request_context


def build_context_compaction(
    *,
    context_window_tokens: int | None,
    max_output_tokens: int | None,
    summary_model: "str | Model | None",
) -> DeepAgentContextCompaction | None:
    if (
        not OPTION.agent.context_compaction_enabled
        or context_window_tokens is None
        or context_window_tokens <= 0
    ):
        return None
    return DeepAgentContextCompaction(
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        summary_model=summary_model,
        summary_timeout=OPTION.agent.context_summary_timeout,
    )


def _build_harness_strategies(
    budget: ContextBudget,
    *,
    summary_model: "str | Model | None",
) -> tuple[TieredCompaction[Any], SlidingWindow[Any]]:
    if budget.target_tokens is None:
        raise ValueError("Context budget has no target")
    target = budget.target_tokens
    max_part_tokens = max(1, int(target * 0.40))
    tiers = [
        ClampOversizedMessages(
            max_part_tokens=max_part_tokens,
            keep_head_chars=2_000,
            keep_tail_chars=2_000,
        ),
        DeduplicateFileReads(file_key=deep_agent_file_key),
        ClearToolResults(
            max_tokens=1,
            keep_pairs=OPTION.agent.context_keep_tool_pairs,
            min_clear_tokens=OPTION.agent.context_min_clear_tokens,
            exclude_tools=frozenset(
                {
                    "builtin__confirm",
                    "builtin__choice",
                    "deep_cube__clarify",
                }
            ),
        ),
        SummarizingCompaction(
            model=summary_model,
            max_tokens=1,
            keep_tokens=max(1, int(target * 0.45)),
            preserve_first_user_message=True,
            incremental=True,
        ),
    ]
    return (
        TieredCompaction(tiers=tiers, target_tokens=target),
        SlidingWindow(
            max_tokens=1,
            keep_tokens=target,
            preserve_first_user_message=True,
        ),
    )
