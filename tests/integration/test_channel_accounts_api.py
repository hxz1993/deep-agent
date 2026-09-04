from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.api.v1 import api_router
from deep_agent.api.v1.channel_auth import ChannelRequestUserDep
from deep_agent.core.context import AppContext, get_context, init_context, reset_context
from deep_agent.security import ConsoleAdminAuthorizer
from tests.helpers.admin_auth import allow_console_admin
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class ChannelAccountsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
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

    async def test_admin_configures_session_user_without_returning_secret(self) -> None:
        created = await self.request("POST", "/api/v1/channel-accounts", json=self.payload())
        self.assertEqual(created.status_code, 201, created.text)
        account = created.json()
        self.assertEqual(account["session_user_id"], "session-user")
        self.assertNotIn("owner_user_id", account)
        self.assertTrue(account["secret_set"])
        self.assertNotIn("secret", account)
        self.assertNotIn("plain-secret", created.text)
        self.assertTrue({"app_id", "space_id", "language"}.isdisjoint(account))

        self.assertEqual(
            (await self.request("GET", "/api/v1/channel-accounts", user="other")).json(),
            [account],
        )
        updated = await self.request(
            "PUT",
            f"/api/v1/channel-accounts/{account['id']}",
            json={"name": "企微助手 2", "enabled": True},
        )
        self.assertEqual(updated.status_code, 200, updated.text)
        self.assertTrue(updated.json()["enabled"])
        self.assertTrue(updated.json()["secret_set"])
        self.assertNotIn("plain-secret", updated.text)
        assert self.stores.channel is not None
        stored = await self.stores.channel.get_account(account["id"])
        self.assertEqual(stored.secret, "plain-secret")

        retained = await self.request(
            "PUT",
            f"/api/v1/channel-accounts/{account['id']}",
            json={"secret": ""},
        )
        self.assertEqual(retained.status_code, 200, retained.text)
        stored = await self.stores.channel.get_account(account["id"])
        self.assertEqual(stored.secret, "plain-secret")

    async def test_body_cannot_supply_owner_or_platform_credentials(self) -> None:
        payload = self.payload()
        payload["owner_user_id"] = "forged"
        response = await self.request("POST", "/api/v1/channel-accounts", json=payload)
        self.assertEqual(response.status_code, 422, response.text)

    async def test_session_user_is_required_and_editable_only_by_admin(self):
        payload = self.payload()
        for value in (None, "", "  ", "x" * 129):
            payload["session_user_id"] = value
            response = await self.request("POST", "/api/v1/channel-accounts", json=payload)
            self.assertEqual(response.status_code, 422)
        created = await self.request("POST", "/api/v1/channel-accounts", json=self.payload())
        path = f"/api/v1/channel-accounts/{created.json()['id']}"
        edited = await self.request("PUT", path, json={"session_user_id": " operator "})
        self.assertEqual(edited.json()["session_user_id"], "operator")
        self.app.dependency_overrides.clear()
        get_context().console_admin_authorizer = ConsoleAdminAuthorizer(AsyncMock(return_value=False))
        denied = await self.request("PUT", path, json={"session_user_id": "forged"})
        self.assertIn(denied.status_code, (401, 403, 503))

    async def test_gateway_uses_configured_user_only_with_valid_internal_token(self):
        payload = self.payload()
        payload["enabled"] = True
        created = await self.request("POST", "/api/v1/channel-accounts", json=payload)
        account_id = created.json()["id"]
        with patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "test-internal"}):
            async with AsyncClient(transport=self.transport, base_url="http://test") as client:
                normal = await client.get("/identity-probe", headers={"user": "login-user"})
                self.assertEqual(normal.json()["user"], "login-user")
                headers = {"user": "login-user", "X-Deep-Agent-Channel-Account": account_id}
                self.assertEqual((await client.get("/identity-probe", headers=headers)).status_code, 401)
                headers["X-Deep-Agent-Channel-Token"] = "wrong"
                self.assertEqual((await client.get("/identity-probe", headers=headers)).status_code, 401)
                headers["X-Deep-Agent-Channel-Token"] = "test-internal"
                authenticated = await client.get("/identity-probe", headers=headers)
                self.assertEqual(authenticated.json()["user"], "session-user")

    async def test_channel_does_not_accept_request_context_fields(self) -> None:
        for field in ("app_id", "space_id", "language"):
            payload = self.payload()
            payload[field] = "unexpected"
            response = await self.request("POST", "/api/v1/channel-accounts", json=payload)
            self.assertEqual(response.status_code, 422, response.text)

    async def test_runtime_reports_not_deployed_and_connection_test_is_503(self) -> None:
        created = await self.request("POST", "/api/v1/channel-accounts", json=self.payload())
        account_id = created.json()["id"]
        with patch.dict(
            os.environ,
            {"DEEP_AGENT_CHANNEL_GATEWAY_URL": ""},
            clear=False,
        ):
            runtime = await self.request("GET", "/api/v1/channel-runtime")
            tested = await self.request(
                "POST", f"/api/v1/channel-accounts/{account_id}/test-connection"
            )
        self.assertEqual(runtime.status_code, 200)
        self.assertEqual(runtime.json()["state"], "not_deployed")
        self.assertEqual(tested.status_code, 503)

    async def test_used_account_cannot_be_deleted(self) -> None:
        created = await self.request("POST", "/api/v1/channel-accounts", json=self.payload())
        account_id = created.json()["id"]
        assert self.stores.channel is not None
        await self.stores.channel.resolve_binding(
            account_id=account_id,
            peer_type="single",
            peer_id="user-1",
        )
        response = await self.request("DELETE", f"/api/v1/channel-accounts/{account_id}")
        self.assertEqual(response.status_code, 409, response.text)


if __name__ == "__main__":
    unittest.main()
