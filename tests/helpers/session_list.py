"""Reusable fixtures without TestCase inheritance or test methods."""
from __future__ import annotations

import asyncio

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deep_agent.api.v1 import api_router
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.mcp.lease_manager import McpLeaseManager
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.schemas.agent import AgentCreate
from tests.helpers.sqlite_stores import make_sqlite_stores_sync


class SessionListFixture:
    def setUp(self) -> None:
        self.user_id = "user-a"
        self.stores, self.engine, self.session_factory = make_sqlite_stores_sync()
        async def seed_agents() -> tuple[str, str]:
            model = (await self.stores.llm_model.list_all())[0]
            agent_a = await self.stores.agent.create_unique(
                AgentCreate(name="session-list-a", model_id=model.id, prompt="test")
            )
            agent_b = await self.stores.agent.create_unique(
                AgentCreate(name="session-list-b", model_id=model.id, prompt="test")
            )
            return agent_a.id, agent_b.id

        self.agent_a, self.agent_b = asyncio.run(seed_agents())
        self.app = FastAPI()
        self.app.include_router(api_router)
        ctx = AppContext.for_testing(
            registry=build_registry(),
            stores=self.stores,
            mcp_lease_manager=McpLeaseManager(),
            db_engine=self.engine,
            db_session_factory=self.session_factory,
        )
        init_context(ctx)
        self.client = TestClient(self.app, headers={"user": self.user_id})

    def tearDown(self) -> None:
        reset_context()
        asyncio.run(self.engine.dispose())
