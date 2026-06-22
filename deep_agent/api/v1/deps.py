from __future__ import annotations

from pydantic_ai.usage import UsageLimits
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.agent.factory import build_agent
from deep_agent.agent.limits import resolve_agent_max_requests
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.spec import AgentSpec, default_spec
from deep_agent.core.context import AppContext
from deep_agent.core.exceptions import AgentNotFoundError
from deep_agent.db.repositories.agent import AgentRepository
from deep_agent.options import OPTION

_agent_repo = AgentRepository()


def build_runner(
    ctx: AppContext,
    *,
    model: str | None = None,
    capabilities: list[str] | None = None,
) -> AgentRunner:
    """Assemble a per-request AgentRunner from settings + registry + store."""
    spec = default_spec()
    if model:
        spec.model = model
    if capabilities is not None:
        spec.capabilities = capabilities

    return _build_runner_from_spec(spec, ctx)


async def build_runner_for_agent(ctx: AppContext, db: AsyncSession, agent_id: str) -> AgentRunner:
    """Assemble a per-request AgentRunner from a database agent configuration."""
    row = await _agent_repo.get_by_id(db, agent_id)
    if row is None:
        raise AgentNotFoundError(agent_id)

    spec = _agent_repo.to_spec(row)
    return _build_runner_from_spec(spec, ctx)


def _build_runner_from_spec(spec: AgentSpec, ctx: AppContext) -> AgentRunner:
    agent = build_agent(spec, ctx.registry, request_retries=OPTION.agent.request_retries)
    limits = UsageLimits(
        request_limit=resolve_agent_max_requests(spec.max_requests, OPTION.agent.max_requests),
        total_tokens_limit=OPTION.agent.max_total_tokens,
    )
    return AgentRunner(agent, ctx.store, limits)
