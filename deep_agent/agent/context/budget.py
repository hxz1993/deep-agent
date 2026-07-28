from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import quantiles

from pydantic_ai.messages import ModelMessage, ModelResponse
from pydantic_ai_harness.compaction import estimate_token_count

from deep_agent.options import OPTION


@dataclass(frozen=True)
class ContextBudget:
    enabled: bool
    reason: str
    context_window_tokens: int | None
    max_output_tokens: int | None
    predicted_output_tokens: int | None
    safety_margin_tokens: int | None
    usable_request_tokens: int | None
    trigger_tokens: int | None
    target_tokens: int | None
    estimated_history_tokens: int

    def as_dict(self) -> dict[str, int | str | bool | None]:
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "context_window_tokens": self.context_window_tokens,
            "max_output_tokens": self.max_output_tokens,
            "predicted_output_tokens": self.predicted_output_tokens,
            "safety_margin_tokens": self.safety_margin_tokens,
            "usable_request_tokens": self.usable_request_tokens,
            "trigger_tokens": self.trigger_tokens,
            "target_tokens": self.target_tokens,
            "estimated_history_tokens": self.estimated_history_tokens,
        }


def _recent_output_tokens(messages: list[ModelMessage], *, sample_size: int = 8) -> list[int]:
    values: list[int] = []
    for message in reversed(messages):
        if not isinstance(message, ModelResponse):
            continue
        usage = message.usage
        value = getattr(usage, "output_tokens", None)
        if value is None:
            value = getattr(usage, "response_tokens", None)
        if isinstance(value, int) and value > 0:
            values.append(value)
        if len(values) >= sample_size:
            break
    return list(reversed(values))


def predict_output_tokens(
    messages: list[ModelMessage],
    *,
    max_output_tokens: int | None,
    output_floor: int,
) -> int:
    """Predict a practical reserve; the model's maximum is only a hard ceiling."""
    samples = _recent_output_tokens(messages)
    if not samples:
        predicted = output_floor
    elif len(samples) == 1:
        predicted = max(output_floor, samples[0])
    else:
        # An upper quartile reacts to consistently long answers without letting
        # one 384K capability declaration consume the entire input budget.
        predicted = max(output_floor, math.ceil(quantiles(samples, n=4)[2]))
    if max_output_tokens is not None:
        predicted = min(predicted, max_output_tokens)
    return max(1, predicted)


def build_context_budget(
    messages: list[ModelMessage],
    *,
    context_window_tokens: int | None,
    max_output_tokens: int | None,
) -> ContextBudget:
    estimated = estimate_token_count(messages)
    if not OPTION.agent.context_compaction_enabled:
        return ContextBudget(
            enabled=False,
            reason="disabled_by_environment",
            context_window_tokens=context_window_tokens,
            max_output_tokens=max_output_tokens,
            predicted_output_tokens=None,
            safety_margin_tokens=None,
            usable_request_tokens=None,
            trigger_tokens=None,
            target_tokens=None,
            estimated_history_tokens=estimated,
        )
    if context_window_tokens is None or context_window_tokens <= 0:
        return ContextBudget(
            enabled=False,
            reason="context_window_unknown",
            context_window_tokens=context_window_tokens,
            max_output_tokens=max_output_tokens,
            predicted_output_tokens=None,
            safety_margin_tokens=None,
            usable_request_tokens=None,
            trigger_tokens=None,
            target_tokens=None,
            estimated_history_tokens=estimated,
        )

    predicted_output = predict_output_tokens(
        messages,
        max_output_tokens=max_output_tokens,
        output_floor=OPTION.agent.context_output_token_floor,
    )
    safety_margin = max(1024, math.ceil(context_window_tokens * 0.05))
    usable = max(1, context_window_tokens - predicted_output - safety_margin)
    trigger = max(1, math.floor(usable * OPTION.agent.context_compaction_trigger_ratio))
    target = max(1, math.floor(usable * OPTION.agent.context_compaction_target_ratio))
    return ContextBudget(
        enabled=True,
        reason="enabled",
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        predicted_output_tokens=predicted_output,
        safety_margin_tokens=safety_margin,
        usable_request_tokens=usable,
        trigger_tokens=trigger,
        target_tokens=target,
        estimated_history_tokens=estimated,
    )
