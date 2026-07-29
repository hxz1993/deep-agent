from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from deep_agent.agent.context.budget import build_context_budget
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.context.test_scenarios import (
    CONTEXT_TEST_SCENARIOS,
    build_context_test_history,
    get_context_test_scenario,
)
from deep_agent.api.v1.request_identity import RequestUserDep
from deep_agent.core.context import AppContextDep
from deep_agent.options import OPTION
from deep_agent.schemas.chat import SessionInfo

router = APIRouter()


class ContextTestScenarioInfo(BaseModel):
    key: str
    title: str
    description: str
    recommended_prompt: str
    expected_behavior: str


class ContextTestScenarioList(BaseModel):
    agent_id: str
    model_name: str
    context_window_tokens: int
    max_output_tokens: int | None = None
    trigger_tokens: int
    target_tokens: int
    scenarios: list[ContextTestScenarioInfo]


class ContextTestSessionCreate(BaseModel):
    agent_id: str = Field(description="Agent used by the generated test session.")


class ContextTestSessionResult(BaseModel):
    session: SessionInfo
    scenario_key: str
    estimated_history_tokens: int
    trigger_tokens: int
    target_tokens: int
    recommended_prompt: str
    expected_behavior: str


def _ensure_enabled() -> None:
    if not OPTION.agent.context_test_scenarios_enabled:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Context test scenarios are disabled.",
        )


async def _agent_and_estimator(ctx: AppContextDep, agent_id: str):
    row = await ctx.stores.agent.get_by_id(agent_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Agent not found.",
        )
    model = row.llm_model
    if model.context_window_tokens is None or model.context_window_tokens <= 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="当前 Agent 的模型未配置上下文窗口，无法生成压缩测试会话。",
        )
    return row, model, TokenEstimator.for_model(model.model, model.base_url)


@router.get(
    "/context-test-scenarios",
    response_model=ContextTestScenarioList,
)
async def list_context_test_scenarios(
    ctx: AppContextDep,
    _user: RequestUserDep,
    agent_id: str = Query(description="Agent used to calculate the live context budget."),
) -> ContextTestScenarioList:
    _ensure_enabled()
    _agent, model, estimator = await _agent_and_estimator(ctx, agent_id)
    budget = build_context_budget(
        [],
        context_window_tokens=model.context_window_tokens,
        max_output_tokens=model.max_output_tokens,
        estimator=estimator,
        estimate_scope="history_only",
    )
    assert budget.trigger_tokens is not None
    assert budget.target_tokens is not None
    return ContextTestScenarioList(
        agent_id=agent_id,
        model_name=model.name,
        context_window_tokens=model.context_window_tokens,
        max_output_tokens=model.max_output_tokens,
        trigger_tokens=budget.trigger_tokens,
        target_tokens=budget.target_tokens,
        scenarios=[
            ContextTestScenarioInfo(
                key=scenario.key,
                title=scenario.title,
                description=scenario.description,
                recommended_prompt=scenario.recommended_prompt,
                expected_behavior=scenario.expected_behavior,
            )
            for scenario in CONTEXT_TEST_SCENARIOS
        ],
    )


@router.post(
    "/context-test-scenarios/{scenario_key}",
    response_model=ContextTestSessionResult,
    status_code=status.HTTP_201_CREATED,
)
async def create_context_test_session(
    ctx: AppContextDep,
    user: RequestUserDep,
    scenario_key: str,
    body: ContextTestSessionCreate,
) -> ContextTestSessionResult:
    _ensure_enabled()
    scenario = get_context_test_scenario(scenario_key)
    if scenario is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Unknown context test scenario.",
        )
    agent, model, estimator = await _agent_and_estimator(ctx, body.agent_id)
    built = build_context_test_history(
        scenario,
        context_window_tokens=model.context_window_tokens,
        max_output_tokens=model.max_output_tokens,
        estimator=estimator,
    )
    session_id = f"context-test-{scenario.key}-{uuid.uuid4().hex}"
    title = f"【压缩测试】{scenario.title}"
    await ctx.store.append(
        session_id,
        built.messages,
        agent_id=agent.id,
        user_id=user,
    )
    await ctx.store.update_session_title(session_id, agent.id, title)

    assert built.budget.trigger_tokens is not None
    assert built.budget.target_tokens is not None
    return ContextTestSessionResult(
        session=SessionInfo(
            session_id=session_id,
            message_count=len(built.messages),
            agent_id=agent.id,
            agent_name=agent.name,
            user_prompt=title,
            title=title,
        ),
        scenario_key=scenario.key,
        estimated_history_tokens=built.budget.estimated_history_tokens,
        trigger_tokens=built.budget.trigger_tokens,
        target_tokens=built.budget.target_tokens,
        recommended_prompt=scenario.recommended_prompt,
        expected_behavior=scenario.expected_behavior,
    )
