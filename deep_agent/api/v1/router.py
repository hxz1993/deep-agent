from __future__ import annotations

from fastapi import APIRouter

from deep_agent.api.v1 import agents, chat, mcp, sessions, tools

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(agents.router, tags=["agents"])
api_router.include_router(mcp.router, tags=["mcp"])
api_router.include_router(chat.router, tags=["chat"])
api_router.include_router(tools.router, tags=["capabilities"])
api_router.include_router(sessions.router, tags=["sessions"])
