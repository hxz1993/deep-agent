"""Reusable fixtures without TestCase inheritance or test methods."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.api.v1 import api_router
from deep_agent.api.v1.channel_auth import ChannelRequestUserDep
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class ChannelAccountsFixture:
    async def asyncSetUp(self) -> None:
        self.key_identity = patch(
            "deep_agent.api.v1.channel_accounts.PlatformEnterpriseAdminChecker.resolve_api_key_user",
            new_callable=AsyncMock, return_value="session-user",
        )
        self.owner_check = self.key_identity.start()
        self.addCleanup(self.key_identity.stop)
        self.stores, self.engine, factory = await make_sqlite_stores()
        self.agent_id = await seed_default_agent(self.stores)
        self.app = FastAPI()
        self.app.include_router(api_router)
        @self.app.get("/identity-probe")
        async def identity_probe(user: ChannelRequestUserDep):
            return {"user": user}
        allow_console_admin(self.app)
        init_context(
            AppContext.for_testing(
                stores=self.stores,
                db_engine=self.engine,
                db_session_factory=factory,
            )
        )
        self.transport = ASGITransport(app=self.app, raise_app_exceptions=False)

    async def asyncTearDown(self) -> None:
        await get_context().event_persister.aclose()
        reset_context()
        await self.engine.dispose()

    async def request(self, method: str, path: str, *, user: str = "owner", json=None):
        async with AsyncClient(transport=self.transport, base_url="http://test") as client:
            return await client.request(method, path, headers={"user": user}, json=json)

    def payload(self) -> dict:
        return {
            "name": "企微助手",
            "bot_id": "bot-id",
            "secret": "plain-secret",
            "agent_id": self.agent_id,
            "session_user_id": "session-user",
            "enabled": False,
        }
