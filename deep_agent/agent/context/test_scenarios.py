from __future__ import annotations

import math
from dataclasses import dataclass

from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from pydantic_ai.usage import RequestUsage

from deep_agent.agent.context.budget import ContextBudget, build_context_budget
from deep_agent.agent.context.estimator import TokenEstimator


@dataclass(frozen=True)
class ContextTestScenario:
    key: str
    title: str
    description: str
    recommended_prompt: str
    expected_behavior: str
    user_template: str
    assistant_template: str


@dataclass(frozen=True)
class BuiltContextTestHistory:
    messages: list[ModelMessage]
    budget: ContextBudget


CONTEXT_TEST_SCENARIOS: tuple[ContextTestScenario, ...] = (
    ContextTestScenario(
        key="long_chinese_summary",
        title="长中文项目记录",
        description="构造多轮中文项目背景，验证主模型摘要和 Checkpoint 写入。",
        recommended_prompt="请总结完整历史中的项目目标、关键约束、已有结论和未决事项。",
        expected_behavior="触发单次摘要，达到目标线后继续回答并生成 Checkpoint。",
        user_template=(
            "第{turn}轮项目补充：目标是交付稳定的上下文压缩能力；"
            "约束是原始消息不可改写、摘要必须保留决策与未决事项。"
        ),
        assistant_template=(
            "第{turn}轮确认：继续保留目标、约束、结论和待办；"
            "当前结论是使用主模型摘要，尚待验证摘要后的目标线。"
        ),
    ),
    ContextTestScenario(
        key="decision_trace",
        title="多轮决策追踪",
        description="构造持续演进的产品决策，验证压缩后能否保留早期决定。",
        recommended_prompt="请按时间顺序列出已经确认的决策，并指出仍未解决的问题。",
        expected_behavior="触发单次摘要并生成 Checkpoint，同时保留首轮任务、稳定决策和最近对话。",
        user_template=(
            "产品评审第{turn}轮：确认测试入口只服务人工复现，"
            "测试会话必须与普通会话隔离且可以直接删除。"
        ),
        assistant_template=(
            "评审记录第{turn}轮：已确认不新增数据库字段，"
            "场景数据按当前消息结构生成；未决项是后续是否扩展异常注入。"
        ),
    ),
    ContextTestScenario(
        key="mixed_content",
        title="中英代码混合记录",
        description="构造中文、英文、JSON 和代码混合历史，观察统一 Token 估算。",
        recommended_prompt="请提取历史中的配置约束、代码约定和最终验证结论。",
        expected_behavior="按保守估算触发单次摘要并生成 Checkpoint，同时展示 Provider 误差。",
        user_template=(
            '第{turn}轮配置核对：context_window=true，language="zh-cn"，'
            'JSON={{"compaction_event":"replace_prefix","raw_events":"append"}}。'
        ),
        assistant_template=(
            "Round {turn} result: keep raw messages immutable; "
            "use `summary = previous + delta`; validation_status = pending。"
        ),
    ),
)

_SCENARIOS_BY_KEY = {scenario.key: scenario for scenario in CONTEXT_TEST_SCENARIOS}
_FILLER_BY_SCENARIO = {
    "long_chinese_summary": "本段用于形成足够长的合法会话历史，同时保持信息可以被摘要。",
    "decision_trace": "每轮评审都会重申稳定决定，并补充一项可追踪的阶段说明。",
    "mixed_content": 'trace={"kind":"context","status":"active"}; 保留结构、标识和结论。',
}
_FILLER_REPETITIONS = 90
_TARGET_OVER_TRIGGER_RATIO = 1.08
_MAX_TURNS = 500


def get_context_test_scenario(key: str) -> ContextTestScenario | None:
    return _SCENARIOS_BY_KEY.get(key)


def build_context_test_history(
    scenario: ContextTestScenario,
    *,
    context_window_tokens: int,
    max_output_tokens: int | None,
    estimator: TokenEstimator,
) -> BuiltContextTestHistory:
    """Build schema-valid dialogue history just beyond the live trigger line."""

    messages: list[ModelMessage] = [
        ModelRequest(
            parts=[
                UserPromptPart(
                    content=f"【压缩测试】{scenario.title}：请持续记录后续项目讨论。"
                )
            ]
        ),
        ModelResponse(
            parts=[TextPart(content="已开始记录，后续会持续维护目标、约束、结论和未决事项。")],
            usage=RequestUsage(output_tokens=64),
        ),
    ]
    filler = _FILLER_BY_SCENARIO[scenario.key]
    repeated_filler = filler * _FILLER_REPETITIONS

    for turn in range(1, _MAX_TURNS + 1):
        messages.extend(
            [
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content=(
                                scenario.user_template.format(turn=turn)
                                + repeated_filler
                            )
                        )
                    ]
                ),
                ModelResponse(
                    parts=[
                        TextPart(
                            content=(
                                scenario.assistant_template.format(turn=turn)
                                + repeated_filler
                            )
                        )
                    ],
                    usage=RequestUsage(output_tokens=256),
                ),
            ]
        )
        budget = build_context_budget(
            messages,
            context_window_tokens=context_window_tokens,
            max_output_tokens=max_output_tokens,
            estimator=estimator,
            output_history=messages,
            estimate_scope="history_only",
        )
        if (
            budget.trigger_tokens is not None
            and budget.estimated_request_tokens
            >= math.ceil(budget.trigger_tokens * _TARGET_OVER_TRIGGER_RATIO)
        ):
            return BuiltContextTestHistory(messages=messages, budget=budget)

    raise RuntimeError(
        f"Unable to build context test scenario within {_MAX_TURNS} turns"
    )
