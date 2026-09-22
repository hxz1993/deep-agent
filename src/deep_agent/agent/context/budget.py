from __future__ import annotations

import math
from dataclasses import dataclass

from pydantic_ai.messages import ModelMessage, ModelResponse

from deep_agent.agent.context.estimator import (
    REQUEST_ENVELOPE_TOKENS,
    TokenEstimator,
)
from deep_agent.options import OPTION

OUTPUT_PREDICTION_FLOOR = 2_048


@dataclass(frozen=True)
class OutputTokenSample:
    tokens: int
    usage_source: str
    estimator_source: str | None = None


@dataclass(frozen=True)
class OutputPrediction:
    tokens: int
    source: str
    sample_count: int
    provider_sample_count: int
    estimated_sample_count: int

    def as_dict(self) -> dict[str, int | str]:
        return {
            "predicted_output_tokens": self.tokens,
            "output_prediction_source": self.source,
            "output_prediction_sample_count": self.sample_count,
            "provider_sample_count": self.provider_sample_count,
            "estimated_sample_count": self.estimated_sample_count,
        }


@dataclass(frozen=True)
class ContextBudget:
    enabled: bool
    reason: str
    context_window_tokens: int | None
    max_output_tokens: int | None
    predicted_output_tokens: int | None
    output_prediction_source: str | None
    output_prediction_sample_count: int
    provider_sample_count: int
    estimated_sample_count: int
    estimator_source: str
    estimate_scope: str
    estimated_history_tokens: int
    estimated_message_tokens: int
    estimated_tool_schema_tokens: int | None
    estimated_request_tokens: int
    safety_margin_tokens: int | None
    usable_request_tokens: int | None
    trigger_tokens: int | None
    target_tokens: int | None
    context_occupancy_tokens: int | None = None
    occupancy_source: str = "estimated"
    usage_anchor_id: str | None = None
    estimation_fallback_reason: str | None = None

    def as_dict(self) -> dict[str, int | float | str | bool | None]:
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "context_window_tokens": self.context_window_tokens,
            "max_output_tokens": self.max_output_tokens,
            "predicted_output_tokens": self.predicted_output_tokens,
            "output_prediction_source": self.output_prediction_source,
            "output_prediction_sample_count": self.output_prediction_sample_count,
            "provider_sample_count": self.provider_sample_count,
            "estimated_sample_count": self.estimated_sample_count,
            "estimator_source": self.estimator_source,
            "estimate_scope": self.estimate_scope,
            "estimated_history_tokens": self.estimated_history_tokens,
            "estimated_message_tokens": self.estimated_message_tokens,
            "estimated_tool_schema_tokens": self.estimated_tool_schema_tokens,
            "estimated_request_tokens": self.estimated_request_tokens,
            "safety_margin_tokens": self.safety_margin_tokens,
            "usable_request_tokens": self.usable_request_tokens,
            "trigger_tokens": self.trigger_tokens,
            "target_tokens": self.target_tokens,
            "context_occupancy_tokens": self.context_occupancy_tokens,
            "occupancy_source": self.occupancy_source,
            "usage_anchor_id": self.usage_anchor_id,
            "estimation_fallback_reason": self.estimation_fallback_reason,
        }


def recent_output_token_samples(
    messages: list[ModelMessage],
    *,
    estimator: TokenEstimator,
    sample_size: int = 8,
) -> list[OutputTokenSample]:
    values: list[OutputTokenSample] = []
    for message in reversed(messages):
        if not isinstance(message, ModelResponse):
            continue
        usage = message.usage
        value = getattr(usage, "output_tokens", None)
        if value is None:
            value = getattr(usage, "response_tokens", None)
        if isinstance(value, int) and value > 0:
            values.append(OutputTokenSample(value, "provider"))
        else:
            values.append(
                OutputTokenSample(
                    estimator.response_output(message),
                    "estimated",
                    estimator.source,
                )
            )
        if len(values) >= sample_size:
            break
    return list(reversed(values))


def _prediction_source(samples: list[OutputTokenSample]) -> str:
    sources = {sample.usage_source for sample in samples}
    if not sources:
        return "floor"
    if sources == {"provider"}:
        return "provider"
    if sources == {"estimated"}:
        return "estimated"
    return "mixed"


def predict_output(
    messages: list[ModelMessage],
    *,
    estimator: TokenEstimator,
    max_output_tokens: int | None,
    additional_samples: list[OutputTokenSample] | None = None,
) -> OutputPrediction:
    samples = [
        *recent_output_token_samples(messages, estimator=estimator),
        *(additional_samples or []),
    ][-8:]
    if not samples:
        predicted = OUTPUT_PREDICTION_FLOOR
    elif len(samples) == 1:
        predicted = max(OUTPUT_PREDICTION_FLOOR, samples[0].tokens)
    else:
        ordered = sorted(sample.tokens for sample in samples)
        rank = max(1, math.ceil(len(ordered) * 0.75))
        predicted = max(OUTPUT_PREDICTION_FLOOR, ordered[rank - 1])
    if max_output_tokens is not None:
        predicted = min(predicted, max_output_tokens)
    provider_count = sum(sample.usage_source == "provider" for sample in samples)
    estimated_count = len(samples) - provider_count
    return OutputPrediction(
        tokens=max(1, predicted),
        source=_prediction_source(samples),
        sample_count=len(samples),
        provider_sample_count=provider_count,
        estimated_sample_count=estimated_count,
    )


def predict_output_tokens(
    messages: list[ModelMessage],
    *,
    max_output_tokens: int | None,
    output_floor: int | None = None,
    estimator: TokenEstimator | None = None,
) -> int:
    """Compatibility wrapper; the reserve floor is now an internal policy."""
    del output_floor
    return predict_output(
        messages,
        estimator=estimator or TokenEstimator.conservative(),
        max_output_tokens=max_output_tokens,
    ).tokens


def build_context_budget(
    messages: list[ModelMessage],
    *,
    context_window_tokens: int | None,
    max_output_tokens: int | None,
    estimator: TokenEstimator | None = None,
    output_history: list[ModelMessage] | None = None,
    additional_output_samples: list[OutputTokenSample] | None = None,
    request_parameters=None,
    estimate_scope: str | None = None,
    precomputed_message_tokens: int | None = None,
) -> ContextBudget:
    estimator = estimator or TokenEstimator.conservative()
    estimate = None
    if precomputed_message_tokens is None:
        estimate = estimator.estimate_request(messages, request_parameters)
        estimated_request, message_tokens, parameter_tokens = (
            estimate.tokens, estimate.message_tokens, estimate.parameter_tokens)
    else:
        message_tokens = max(0, int(precomputed_message_tokens))
        parameter_tokens = estimator.request_parameters(request_parameters)
        estimated_request = (
            message_tokens + parameter_tokens + REQUEST_ENVELOPE_TOKENS
        )
    scope = estimate_scope or (
        "full_model_request" if request_parameters is not None else "history_only"
    )
    estimate_fields = dict(
        context_occupancy_tokens=estimated_request,
        occupancy_source=estimate.source if estimate else "estimated",
        usage_anchor_id=estimate.anchor_id if estimate else None,
        estimation_fallback_reason=estimate.fallback_reason if estimate else "usage_unavailable",
    )
    prediction = predict_output(
        output_history if output_history is not None else messages,
        estimator=estimator,
        max_output_tokens=max_output_tokens,
        additional_samples=additional_output_samples,
    )

    if context_window_tokens is None or context_window_tokens <= 0:
        return ContextBudget(
            **estimate_fields,
            enabled=False,
            reason="context_window_unknown",
            context_window_tokens=context_window_tokens,
            max_output_tokens=max_output_tokens,
            predicted_output_tokens=None,
            output_prediction_source=None,
            output_prediction_sample_count=prediction.sample_count,
            provider_sample_count=prediction.provider_sample_count,
            estimated_sample_count=prediction.estimated_sample_count,
            estimator_source=estimator.source,
            estimate_scope=scope,
            estimated_history_tokens=message_tokens,
            estimated_message_tokens=message_tokens,
            estimated_tool_schema_tokens=(
                parameter_tokens if request_parameters is not None else None
            ),
            estimated_request_tokens=estimated_request,
            safety_margin_tokens=None,
            usable_request_tokens=None,
            trigger_tokens=None,
            target_tokens=None,
        )

    error_ratio = 0.05 if estimator.source == "model_tokenizer" else 0.10
    safety_margin = max(1_024, math.ceil(estimated_request * error_ratio))
    usable = max(
        1,
        context_window_tokens - prediction.tokens - safety_margin,
    )
    trigger = max(
        1,
        math.floor(usable * OPTION.agent.context_compaction_trigger_ratio),
    )
    target = max(
        1,
        math.floor(usable * OPTION.agent.context_compaction_target_ratio),
    )
    return ContextBudget(
        **estimate_fields,
        enabled=True,
        reason="enabled",
        context_window_tokens=context_window_tokens,
        max_output_tokens=max_output_tokens,
        predicted_output_tokens=prediction.tokens,
        output_prediction_source=prediction.source,
        output_prediction_sample_count=prediction.sample_count,
        provider_sample_count=prediction.provider_sample_count,
        estimated_sample_count=prediction.estimated_sample_count,
        estimator_source=estimator.source,
        estimate_scope=scope,
        estimated_history_tokens=message_tokens,
        estimated_message_tokens=message_tokens,
        estimated_tool_schema_tokens=(
            parameter_tokens if request_parameters is not None else None
        ),
        estimated_request_tokens=estimated_request,
        safety_margin_tokens=safety_margin,
        usable_request_tokens=usable,
        trigger_tokens=trigger,
        target_tokens=target,
    )
