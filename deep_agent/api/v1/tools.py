from __future__ import annotations

from fastapi import APIRouter

from deep_agent.core.context import AppContextDep
from deep_agent.schemas.chat import CapabilityDescription

router = APIRouter()


@router.get("/tools", response_model=list[CapabilityDescription])
async def list_capabilities(ctx: AppContextDep) -> list[CapabilityDescription]:
    """List all registered capabilities (native / skill / mcp) and their tools."""
    return [CapabilityDescription(**cap) for cap in ctx.registry.describe()]
