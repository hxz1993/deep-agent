from __future__ import annotations

from uuid import UUID

from fastapi import Request
from pydantic_ai.usage import UsageLimits
from sqlalchemy.ext.asyncio import AsyncSession

from deep_agent.agent.factory import build_agent
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.spec import AgentSpec, default_spec
from deep_agent.capabilities.registry import CapabilityRegistry
from deep_agent.core.config import Settings
from deep_agent.core.exceptions import AgentNotFoundError
from deep_agent.db.repositories.agent import AgentRepository
from deep_agent.memory.base import SessionStore

_agent_repo = AgentRepository()


def get_settings(request: Request) -> Settings:
    return request.app.state.settings


def get_registry(request: Request) -> CapabilityRegistry:
    return request.app.state.registry


def get_store(request: Request) -> SessionStore:
    return request.app.state.store


def build_runner(
    request: Request,
    *,
    model: str | None = None,
    capabilities: list[str] | None = None,
) -> AgentRunner:
    """Assemble a per-request AgentRunner from settings + registry + store."""
    settings = get_settings(request)
    registry = get_registry(request)
    store = get_store(request)

    spec = default_spec(settings)
    if model:
        spec.model = model
    if capabilities is not None:
        spec.capabilities = capabilities

    return _build_runner_from_spec(spec, settings, registry, store)


async def build_runner_for_agent(request: Request, db: AsyncSession, agent_id: UUID) -> AgentRunner:
    """Assemble a per-request AgentRunner from a database agent configuration."""
    settings = get_settings(request)
    registry = get_registry(request)
    store = get_store(request)

    row = await _agent_repo.get_by_id(db, agent_id)
    if row is None:
        raise AgentNotFoundError(agent_id)

    spec = _agent_repo.to_spec(row)
    return _build_runner_from_spec(spec, settings, registry, store)


def _build_runner_from_spec(
    spec: AgentSpec,
    settings: Settings,
    registry: CapabilityRegistry,
    store: SessionStore,
) -> AgentRunner:
    agent = build_agent(spec, registry)
    limits = UsageLimits(
        request_limit=settings.max_requests,
        total_tokens_limit=settings.max_total_tokens,
    )
    return AgentRunner(agent, store, limits)
