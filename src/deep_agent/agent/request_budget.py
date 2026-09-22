"""A user-turn request allowance, with the final request reserved for an answer."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from typing import Any

from pydantic_ai.capabilities import AbstractCapability, CapabilityOrdering
from pydantic_ai.messages import InstructionPart, ModelRequest, ModelResponse, ToolCallPart
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.db.event_codec import group_message_events

BUDGET_METADATA = "deepagent_request_budget"


def restored_request_count(events: list[Any]) -> int:
    """Count SDK responses, not part events; checkpoints include auxiliary requests.

    Checkpoints are local to a Run, so restoring them never duplicates previous
    Runs' usage. Legacy compactions have a cumulative summary_calls telemetry or
    a completed summary_call. Neither titles nor tool results spend requests.
    """
    responses: dict[str, int] = defaultdict(int)
    checkpoints: dict[str, int] = defaultdict(int)
    summaries: dict[str, int] = defaultdict(int)
    summary_highwater: dict[str, int] = defaultdict(int)
    for event in group_message_events(events):
        get = event.get if isinstance(event, dict) else lambda key, default=None: getattr(event, key, default)
        run_id = get("run_id") or "legacy"
        payload = get("payload") or {}
        message = payload.get("message") or {}
        if message.get("kind") == "response":
            responses[run_id] += 1
            checkpoint = (message.get("metadata") or {}).get(BUDGET_METADATA) or {}
            requests = checkpoint.get("run_requests")
            if isinstance(requests, int) and not isinstance(requests, bool) and requests >= 0:
                checkpoints[run_id] = max(checkpoints[run_id], requests)
        if get("event_type") == "compaction":
            calls = (payload.get("budget") or {}).get("summary_calls")
            if isinstance(calls, int) and calls >= 0:
                summary_highwater[run_id] = max(summary_highwater[run_id], calls)
            elif payload.get("summary_call"):
                summaries[run_id] += 1
    runs = responses.keys() | checkpoints.keys() | summaries.keys() | summary_highwater.keys()
    return sum(max(checkpoints[run], responses[run] + max(summaries[run], summary_highwater[run]))
               for run in runs)


@dataclass
class TurnRequestBudget(AbstractCapability[AgentDeps]):
    limits: UsageLimits
    previous_requests: int = 0
    final_request: bool = False

    @property
    def run_limits(self) -> UsageLimits:
        limit = self.limits.request_limit
        return replace(self.limits, request_limit=None if limit is None else max(0, limit - self.previous_requests))

    def get_ordering(self) -> CapabilityOrdering:
        # Run after discovery AND compaction, whose summary may spend a request.
        # after_model_request runs in reverse, before message persistence.
        return CapabilityOrdering(position="innermost")

    async def before_model_request(self, ctx, request_context):
        limit = self.limits.request_limit
        if limit is None:
            return request_context
        used = self.previous_requests + ctx.usage.requests
        self.final_request = used >= limit - 1
        if self.final_request:
            parameters = request_context.model_request_parameters
            request_context.model_request_parameters = replace(
                parameters, function_tools=[], native_tools=[], output_tools=[],
                output_mode="text", output_object=None, allow_text_output=True,
                allow_image_output=False, tool_visibility=None, revealed_tool_names=set(),
                deferred_capability_ids=set(), prompted_output_template=None,
            )
            # Clear provider-specific forced choices rather than send a choice
            # naming a tool whose definition has just been removed.
            settings = dict(request_context.model_settings or {})
            for key in tuple(settings):
                if key == "tool_choice" or key.endswith("_tool_choice"):
                    settings.pop(key)
            settings["tool_choice"] = "none"
            request_context.model_settings = settings
            instruction = (
                "本轮已进入单次回复调用次数上限前的最后一次模型请求。不得再调用任何工具。"
                "请使用与用户一致的语言，根据已有结果正常回复：先总结已经完成且有依据的结论，再说明尚未确认或未完成的内容，"
                "不要将未完成的任务说成已完成。最后简短告知用户：本轮已达到单次回复的调用上限，"
                "可以回复‘继续’，在下一轮接着处理。不要暴露内部实现细节。"
            )
        elif used >= max(1, limit * 4 // 5):
            instruction = (
                "本轮剩余调用额度较少，请聚焦关键缺口，优先使用已有证据，准备给出阶段性结论。"
                "不必向用户单独发送额度警告。"
            )
        else:
            return request_context
        # Request-scoped instructions, not a fake user message or tool result.
        messages = list(request_context.messages)
        for index in range(len(messages) - 1, -1, -1):
            if isinstance(messages[index], ModelRequest):
                last = messages[index]
                messages[index] = replace(last, instructions="\n\n".join(filter(None, (last.instructions, instruction))))
                break
        request_context.messages = messages
        request_context.model_request_parameters = replace(
            request_context.model_request_parameters,
            instruction_parts=[*(request_context.model_request_parameters.instruction_parts or []),
                               InstructionPart(content=instruction)],
        )
        return request_context

    async def after_model_request(self, ctx, *, request_context, response: ModelResponse):
        response.metadata = {
            **(response.metadata or {}),
            BUDGET_METADATA: {"run_requests": ctx.usage.requests},
        }
        if self.final_request and any(isinstance(part, ToolCallPart) for part in response.parts):
            # A provider ignoring the no-tools contract must not execute one or
            # silently get an extra model request. This is a real protocol error.
            raise UnexpectedModelBehavior("The final answer request returned a tool call despite having no tools.")
        return response
