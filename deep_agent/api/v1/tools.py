from __future__ import annotations

from fastapi import APIRouter, Request

from deep_agent.api.v1.deps import get_registry
from deep_agent.schemas.chat import CapabilityDescription

router = APIRouter()


@router.get("/tools", response_model=list[CapabilityDescription])
async def list_capabilities(request: Request) -> list[CapabilityDescription]:
    """List all registered capabilities (native / skill / mcp) and their tools."""
    registry = get_registry(request)
    return [CapabilityDescription(**cap) for cap in registry.describe()]
