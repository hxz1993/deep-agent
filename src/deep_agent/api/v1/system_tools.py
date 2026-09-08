from __future__ import annotations

from fastapi import APIRouter

from deep_agent.api.v1.admin import CONSOLE_ADMIN_DEPENDENCIES
from deep_agent.capabilities.system_tools import list_system_tool_domains
from deep_agent.schemas.system_tool import SystemToolDomain

router = APIRouter(dependencies=CONSOLE_ADMIN_DEPENDENCIES)


@router.get("/system-tools", response_model=list[SystemToolDomain])
async def list_system_tools() -> list[SystemToolDomain]:
    """Return read-only runtime system tools without constructing an Agent Run."""
    return list(list_system_tool_domains())
