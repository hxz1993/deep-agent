import os
import unittest
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.schemas.channel import ChannelAccountCreate
from deep_agent.security import ConsoleAdminAuthorizer
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class WecomApplicationsApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, factory = await make_sqlite_stores()
        self.checker = AsyncMock(return_value=True)
        self.ctx = AppContext.for_testing(stores=self.stores, db_engine=self.engine, db_session_factory=factory,
            console_admin_authorizer=ConsoleAdminAuthorizer(self.checker))
        init_context(self.ctx)
        self.app = FastAPI()
        self.app.include_router(api_router)
        self.http = AsyncClient(transport=ASGITransport(app=self.app), base_url="http://test")
        self.headers = {"user": "enterprise-user", "authorization": "Bearer fixture"}
        self.payload = {"name": "测试应用", "corp_id": "fixture-corp", "wecom_agent_id": "100", "app_secret": "fixture-secret-not-public"}
        self.path = "/api/v1/wecom-applications"

    async def asyncTearDown(self):
        await self.http.aclose()
        if self.ctx.wecom_profiles:
            await self.ctx.wecom_profiles.close()
        await self.ctx.event_persister.aclose()
        reset_context()
        await self.engine.dispose()

    async def create(self):
        response = await self.http.post(self.path, json=self.payload, headers=self.headers)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def test_crud_mask_immutable_identity_and_references(self):
        app = await self.create()
        path = self.path + "/" + app["id"]
        self.assertNotIn("app_secret", app)
        self.assertIn("********", app["app_secret_masked"])
        for secret in (None, "", "  ", app["app_secret_masked"]):
            response = await self.http.put(path, json={"app_secret": secret}, headers=self.headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual((await self.stores.wecom.get_application(app["id"])).app_secret, self.payload["app_secret"])
        for field in ("corp_id", "wecom_agent_id"):
            response = await self.http.put(path, json={field: "changed"}, headers=self.headers)
            self.assertEqual(response.status_code, 422)
        agent = await seed_default_agent(self.stores)
        account = await self.stores.channel.create_account(ChannelAccountCreate(name="bot", bot_id="bot", secret="fixture", agent_id=agent, session_user_id="owner", settings={"wecom": {"application_id": app["id"]}}))
        response = await self.http.get(path, headers=self.headers)
        self.assertEqual(response.json()["channel_count"], 1)
        self.assertEqual(response.json()["channels"], [{"id": account.id, "name": "bot"}])
        self.assertEqual((await self.http.delete(path, headers=self.headers)).status_code, 409)
        self.assertEqual((await self.http.get("/api/v1/channel-accounts/"+account.id, headers=self.headers)).status_code, 200)

    async def test_explicit_secret_clear_invalidates_token_and_skips_external_request(self):
        app = await self.create()
        path = self.path + "/" + app["id"]
        profiles = self.ctx.get_wecom_profiles()
        client = profiles.client
        client._request = AsyncMock(return_value={"access_token": "fixture-token", "expires_in": 7200})
        before = await self.stores.wecom.get_application(app["id"])
        await client.token(before)
        self.assertTrue(client._tokens)
        conflict = await self.http.put(path, json={"clear_app_secret": True, "app_secret": "new-secret"}, headers=self.headers)
        self.assertEqual(conflict.status_code, 422)
        response = await self.http.put(path, json={"clear_app_secret": True}, headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["secret_set"])
        self.assertFalse(response.json()["app_secret_masked"])
        after = await self.stores.wecom.get_application(app["id"])
        self.assertEqual(after.app_secret, "")
        self.assertGreater(after.revision, before.revision)
        self.assertFalse(client._tokens)
        client._request.reset_mock()
        result = await self.http.post(path + "/test-connection", headers=self.headers)
        self.assertFalse(result.json()["success"])
        self.assertIn("secret_not_configured", result.json()["message"])
        client._request.assert_not_awaited()
        restored = await self.http.put(path, json={"app_secret": "replacement-secret"}, headers=self.headers)
        self.assertTrue(restored.json()["secret_set"])

    async def test_admin_only_and_safe_validation(self):
        app = await self.create()
        path = self.path + "/" + app["id"]
        response = await self.http.post(self.path, headers=self.headers, json={**self.payload, "app_secret": ["private-input"]})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn("private-input", response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.checker.return_value = False
        user = {"user": "ordinary", "authorization": "Bearer other-fixture"}
        for method, suffix in (("GET", ""), ("GET", "/cache"), ("POST", "/test-connection"), ("POST", "/cache/refresh"), ("POST", "/cache/invalidate"), ("DELETE", "")):
            response = await self.http.request(method, path+suffix, headers=user)
            self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual((await self.http.get(path)).status_code, 401)

    async def test_internal_auth_disabled_and_invalid_binding(self):
        path = "/api/v1/internal/channels/wecom/resolve-user"
        body = {"sender_id": "opaque"}
        self.assertEqual((await self.http.post(path, json=body)).status_code, 401)
        agent = await seed_default_agent(self.stores)
        account = await self.stores.channel.create_account(ChannelAccountCreate(name="bot", bot_id="bot", secret="fixture", agent_id=agent, session_user_id="owner", enabled=True))
        headers = {"X-Deep-Agent-Channel-Token": "fixture-internal", "X-Deep-Agent-Channel-Account": account.id}
        with patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "fixture-internal"}):
            response = await self.http.post(path, json=body, headers=headers)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(response.json()["status"], "disabled")
            response = await self.http.post(path, json={**body, "session_id": "other-session"}, headers=headers)
            self.assertEqual(response.status_code, 403)
            response = await self.http.post(path, json=body, headers={**headers, "X-Deep-Agent-Channel-Token": "bad"})
            self.assertEqual(response.status_code, 401)

    async def test_application_channel_resolves_persisted_member_via_internal_api(self):
        app = await self.create()
        agent = await seed_default_agent(self.stores)
        account = await self.stores.channel.create_account(ChannelAccountCreate(
            name="应用渠道", channel_type="wecom_app", transport="webhook",
            webhook_token="fixture", webhook_aes_key="abcdefghijklmnopqrstuvwxyz0123456789ABCDEFG",
            agent_id=agent, session_user_id="owner", enabled=True,
            settings={"wecom_app": {"application_id": app["id"]}},
        ))
        await self.stores.wecom.upsert_identity(app["id"], 1, "Member")
        await self.stores.wecom.save_profile(app["id"], 1, "Member", "成员姓名")
        headers = {"X-Deep-Agent-Channel-Token": "fixture-internal", "X-Deep-Agent-Channel-Account": account.id}
        with patch.dict(os.environ, {"DEEP_AGENT_CHANNEL_INTERNAL_TOKEN": "fixture-internal"}):
            response = await self.http.post("/api/v1/internal/channels/wecom/resolve-user",
                json={"sender_id": "Member"}, headers=headers)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["name"], "成员姓名")
        bound = await self.stores.channel.resolve_binding(account_id=account.id, peer_type="single", peer_id="Member")
        self.assertEqual(bound.session.title, "wecom_app｜User｜成员姓名(Member)")

    async def test_empty_cache_refresh_and_unknown_job(self):
        app = await self.create()
        path = self.path + "/" + app["id"] + "/cache"
        response = await self.http.get(path, headers=self.headers)
        self.assertEqual(response.json()["total"], 0)
        response = await self.http.post(path + "/refresh", headers=self.headers)
        self.assertEqual(response.status_code, 202)
        import asyncio
        await asyncio.gather(*list(self.ctx.wecom_profiles._bulk_tasks.values()))
        done = await self.http.get(path + "/refresh/" + response.json()["task_id"], headers=self.headers)
        self.assertEqual(done.json()["status"], "completed")
        self.assertEqual((await self.http.get(path + "/refresh/missing", headers=self.headers)).status_code, 404)
        self.assertEqual((await self.http.post(path + "/invalidate", headers=self.headers)).json()["pending"], 0)
