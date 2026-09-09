from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage

from deep_agent.agent.context.budget import ContextBudget
from deep_agent.agent.history import has_unprocessed_tool_calls


@dataclass(frozen=True)
class ManualCompactionAvailability:
    available: bool
    reason: str


def manual_compaction_availability(
    raw_history: list[ModelMessage],
    budget: ContextBudget,
) -> ManualCompactionAvailability:
    """Evaluate whether manual compaction can be started safely.

    Manual compaction is a user action.  Content length, an existing
    prior compaction, or the absence of new messages must not disable the action;
    the compaction result reports whether the attempt had an effect.
    """

    if has_unprocessed_tool_calls(raw_history):
        return ManualCompactionAvailability(False, "pending_deferred")
    if not budget.enabled or budget.usable_request_tokens is None:
        return ManualCompactionAvailability(False, "context_window_unknown")
    return ManualCompactionAvailability(True, "available")
