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

    async def test_all_channel_types_check_owner_before_create(self):
        application = await self.stores.wecom.create_application(
            name="fixture", corp_id="corp", wecom_agent_id="1", app_secret="fixture-secret")
        for channel_type in ("wecom", "feishu", "dingtalk", "wecom_app"):
            with self.subTest(channel_type=channel_type):
                payload = {**self.payload(), "channel_type": channel_type, "settings": {
                    "deepfos": {"enabled": True, "app_id": "demo001", "api_key": "fixture-key"}}}
                if channel_type == "wecom_app":
                    payload.update(transport="webhook", bot_id=None, secret=None,
                                   webhook_token="fixture", webhook_aes_key="A" * 43)
                    payload["settings"]["wecom_app"] = {"application_id": application.id}
                self.owner_check.return_value = "different-user"
                before = len(await self.stores.channel.list_accounts())
                rejected = await self.request("POST", "/api/v1/channel-accounts", json=payload)
                self.assertEqual(rejected.status_code, 422, rejected.text)
                self.assertNotIn("fixture-key", rejected.text)
                self.assertEqual(len(await self.stores.channel.list_accounts()), before)
                self.owner_check.return_value = "session-user"
                saved = await self.request("POST", "/api/v1/channel-accounts", json=payload)
                self.assertEqual(saved.status_code, 201, saved.text)
                self.owner_check.assert_awaited_with("fixture-key", "demo001", "demo")
                path = "/api/v1/channel-accounts/" + saved.json()["id"]
                rejected = await self.request("PUT", path, json={"session_user_id": "different-user"})
                self.assertEqual(rejected.status_code, 422, rejected.text)
                self.assertEqual((await self.request("GET", path)).json(), saved.json())
                updated = await self.request("PUT", path, json={"name": "updated " + channel_type})
                self.assertEqual(updated.status_code, 200, updated.text)


    async def test_key_failure_details_reach_console_without_saving(self):
        from deep_agent.security.console_admin import PlatformApiKeyRejectedError
        payload = {**self.payload(), "settings": {"deepfos": {
            "enabled": True, "app_id": "demo001", "api_key": "fixture-key"}}}
        for reason, expected in (
            ("密钥不存在", "不存在"),
            ("密钥与空间不匹配", "所属空间"),
            ("密钥授权过期", "已停用或过期"),
            ("密钥未授权该应用", "未授权当前关联应用"),
            ("private-upstream-detail", "未返回可识别的原因"),
        ):
            self.owner_check.side_effect = PlatformApiKeyRejectedError(reason)
            response = await self.request("POST", "/api/v1/channel-accounts", json=payload)
            self.assertEqual(response.status_code, 422)
            self.assertIn(expected, response.json()["detail"])
            self.assertNotIn("private-upstream-detail", response.text)
            self.assertNotIn("fixture-key", response.text)
            self.assertEqual(await self.stores.channel.list_accounts(), [])

    async def test_retained_key_rechecks_owner_app_and_failure_is_atomic(self):
        from deep_agent.security.console_admin import ConsoleAdminAuthenticationError, ConsoleAdminUnavailableError
        settings = {"deepfos": {"enabled": True, "app_id": "demo001", "api_key": "fixture-key"}}
        created = await self.request("POST", "/api/v1/channel-accounts", json={**self.payload(), "settings": settings})
        path = "/api/v1/channel-accounts/" + created.json()["id"]
        for patch_body, error, expected in (
            ({"session_user_id": "different-user"}, None, 422),
            ({"name": "must not save"}, ConsoleAdminAuthenticationError("secret"), 422),
            ({"settings": {"deepfos": {"enabled": True, "app_id": "demo002"}}}, ConsoleAdminUnavailableError("secret"), 503),
            ({"settings": {"deepfos": {"enabled": True, "app_id": "demo001", "clear_api_key": True}}}, None, 422),
        ):
            self.owner_check.side_effect = error
            result = await self.request("PUT", path, json=patch_body)
            self.assertEqual(result.status_code, expected, result.text)
            self.assertNotIn("secret", result.text)
            self.assertEqual((await self.request("GET", path)).json(), created.json())
        self.owner_check.side_effect = None
        self.owner_check.reset_mock()
        saved = await self.request("PUT", path, json={"name": "updated"})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.owner_check.assert_awaited_once_with("fixture-key", "demo001", "demo")
        self.owner_check.reset_mock()
        saved = await self.request("PUT", path, json={"settings": {"deepfos": {"enabled": False, "app_id": "demo001", "clear_api_key": True}}})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.owner_check.assert_not_awaited()

    async def test_enabled_requires_key_and_concurrent_change_rejects_stale_save(self):
        settings = {"deepfos": {"enabled": True, "app_id": "demo001"}}
        missing = await self.request("POST", "/api/v1/channel-accounts", json={**self.payload(), "settings": settings})
        self.assertEqual(missing.status_code, 422, missing.text)
        self.owner_check.assert_not_awaited()
        settings["deepfos"]["api_key"] = "fixture-key"
        created = await self.request("POST", "/api/v1/channel-accounts", json={**self.payload(), "settings": settings})
        account_id = created.json()["id"]
        async def concurrent_update(*args):
            from deep_agent.schemas.channel import ChannelAccountUpdate
            await self.stores.channel.update_account(account_id, ChannelAccountUpdate(name="concurrent"))
            return "session-user"
        self.owner_check.side_effect = concurrent_update
        response = await self.request("PUT", "/api/v1/channel-accounts/" + account_id, json={"name": "stale"})
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual((await self.stores.channel.get_account(account_id)).name, "concurrent")

    async def test_same_timestamp_identity_change_rejects_stale_save(self):
        from sqlalchemy import update
        from deep_agent.db.models import ChannelAccount
        settings = {"deepfos": {"enabled": True, "app_id": "demo001", "api_key": "fixture-key"}}
        created = await self.request("POST", "/api/v1/channel-accounts", json={**self.payload(), "settings": settings})
        account_id = created.json()["id"]
        for changes in (
            {"session_user_id": "other-user"},
            {"settings": {"deepfos": {"enabled": True, "app_id": "demo002", "api_key": "other-key"}}},
        ):
            before = await self.stores.channel.get_account(account_id)
            async def concurrent_update(*args):
                # Reproduce MySQL second-precision timestamps without requiring a MySQL fixture.
                async with self.stores.channel._write(exclusive=True) as db:
                    await db.execute(update(ChannelAccount).where(ChannelAccount.id == account_id).values(
                        **changes, updated_at=before.updated_at))
                return before.session_user_id
            self.owner_check.side_effect = concurrent_update
            response = await self.request("PUT", "/api/v1/channel-accounts/" + account_id, json={"name": "stale"})
            self.assertEqual(response.status_code, 409, response.text)
            after = await self.stores.channel.get_account(account_id)
            self.assertEqual(after.updated_at, before.updated_at)
            self.assertEqual(after.name, before.name)
            for key, value in changes.items():
                self.assertEqual(getattr(after, key), value)

    async def test_space_key_is_private_and_supports_keep_replace_clear(self) -> None:
        key = "fixture-only-space-key-0001"
        payload = {**self.payload(), "settings": {"deepfos": {
            "enabled": True, "app_id": "fixture007", "api_key": key,
        }}}
        created = await self.request("POST", "/api/v1/channel-accounts", json=payload)
        self.assertEqual(created.status_code, 201, created.text)
        account_id = created.json()["id"]
        path = f"/api/v1/channel-accounts/{account_id}"
        for response in (created, await self.request("GET", path),
                         await self.request("GET", "/api/v1/channel-accounts")):
            self.assertNotIn(key, response.text)
            self.assertNotIn('"api_key":', response.text)
        config = created.json()["settings"]["deepfos"]
        self.assertTrue(config["api_key_set"])
        self.assertEqual(config["api_key_masked"], "fixtur********0001")
        for patch_config in (config, {**config, "api_key": ""}, {**config, "enabled": False}):
            saved = await self.request("PUT", path, json={"settings": {"deepfos": patch_config}})
            self.assertEqual(saved.status_code, 200, saved.text)
            self.assertTrue(saved.json()["settings"]["deepfos"]["api_key_set"])
            row = await self.stores.channel.get_account(account_id)
            self.assertEqual(row.settings["deepfos"]["api_key"], key)
            self.assertNotIn("api_key_masked", row.settings["deepfos"])
        changed_key = "fixture-only-space-key-0002"
        replaced = await self.request("PUT", path, json={"settings": {"deepfos": {
            **config, "api_key": changed_key,
        }}})
        self.assertEqual(replaced.json()["settings"]["deepfos"]["api_key_masked"], "fixtur********0002")
        cleared = await self.request("PUT", path, json={"settings": {"deepfos": {
            **config, "enabled": False, "clear_api_key": True,
        }}})
        self.assertFalse(cleared.json()["settings"]["deepfos"]["api_key_set"])
        self.assertNotIn("api_key", (await self.stores.channel.get_account(account_id)).settings["deepfos"])

    async def test_space_key_rejects_header_injection(self) -> None:
        for key in ("fixture\r\nInjected: value", "含中文", 123):
            response = await self.request("POST", "/api/v1/channel-accounts", json={
                **self.payload(), "settings": {"deepfos": {"enabled": True, "app_id": "fixture007", "api_key": key}},
            })
            self.assertEqual(response.status_code, 422)
        response = await self.request("POST", "/api/v1/channel-accounts", json={
            **self.payload(), "settings": {"deepfos": {"enabled": True, "app_id": "fixture007",
                "api_key": "fixture-key", "clear_api_key": True}},
        })
        self.assertEqual(response.status_code, 422)

    async def test_admin_configures_session_user_without_returning_secret(self) -> None:
        created = await self.request("POST", "/api/v1/channel-accounts", json=self.payload())
        self.assertEqual(created.status_code, 201, created.text)
        account = created.json()
        self.assertEqual(account["session_user_id"], "session-user")
        self.assertNotIn("owner_user_id", account)
        self.assertTrue(account["secret_set"])
        self.assertEqual(account["secret_masked"], "pla********ret")
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

    async def test_secret_mask_is_safe_for_all_reads_and_changes_after_replacement(self):
        samples = [
            ("abcd", "****"),
            ("abcde", "a********de"),
            ("abc123456xyz", "abc********xyz"),
            ("fixture-secret-for-channel-1234", "fixtur********1234"),
        ]
        for index, (secret, expected_mask) in enumerate(samples):
            with self.subTest(length=len(secret)):
                payload = self.payload()
                payload.update(name=f"示例渠道 {index}", bot_id=f"bot-{index}", secret=secret)
                created = await self.request("POST", "/api/v1/channel-accounts", json=payload)
                self.assertEqual(created.status_code, 201, created.text)
                path = f"/api/v1/channel-accounts/{created.json()['id']}"
                detail = await self.request("GET", path)
                listed = await self.request("GET", "/api/v1/channel-accounts")
                for response in (created, detail):
                    self.assertEqual(response.json()["secret_masked"], expected_mask)
                    self.assertNotIn("secret", response.json())
                    self.assertNotIn(secret, response.text)
                self.assertNotIn(secret, listed.text)
                replacement = "updated-fixture-secret-6789"
                updated = await self.request("PUT", path, json={"secret": replacement})
                self.assertEqual(updated.status_code, 200, updated.text)
                self.assertEqual(updated.json()["secret_masked"], "update********6789")
                self.assertNotIn(replacement, updated.text)
                for value in ("", "   "):
                    retained = await self.request("PUT", path, json={"secret": value})
                    self.assertEqual(retained.json()["secret_masked"], "update********6789")
                stored = await self.stores.channel.get_account(created.json()["id"])
                self.assertEqual(stored.secret, replacement)

    async def test_secret_hint_remains_admin_only(self):
        created = await self.request("POST", "/api/v1/channel-accounts", json=self.payload())
        self.app.dependency_overrides.clear()
        get_context().console_admin_authorizer = ConsoleAdminAuthorizer(AsyncMock(return_value=False))
        for path in ("/api/v1/channel-accounts", f"/api/v1/channel-accounts/{created.json()['id']}"):
            denied = await self.request("GET", path, user="ordinary-user")
            self.assertIn(denied.status_code, (401, 403, 503))
            self.assertNotIn("secret_masked", denied.text)

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

    async def test_deepfos_settings_validate_and_round_trip_without_affecting_extensions(self):
        settings = {"deepfos": {"enabled": True, "app_id": "demo001", "future": "keep", "api_key": "fixture-key"},
                    "wecom": {"user_details_enabled": False}, "custom": {"keep": 1}}
        created = await self.request("POST", "/api/v1/channel-accounts",
                                     json={**self.payload(), "settings": settings})
        self.assertEqual(created.status_code, 201, created.text)
        path = f"/api/v1/channel-accounts/{created.json()['id']}"
        settings["deepfos"].pop("api_key")
        settings["deepfos"].update(api_key_set=True, api_key_masked="fix********key")
        self.assertEqual(created.json()["settings"], settings)
        for config in ({"enabled": True}, {"enabled": "false"}, {"enabled": True, "app_id": "bad"}):
            invalid = await self.request("PUT", path, json={"settings": {"deepfos": config}})
            self.assertEqual(invalid.status_code, 422, invalid.text)
            self.assertEqual((await self.request("GET", path)).json()["settings"], settings)
        settings["deepfos"]["enabled"] = False
        disabled = await self.request("PUT", path, json={"settings": settings})
        self.assertEqual(disabled.status_code, 200, disabled.text)
        self.assertEqual(disabled.json()["settings"], settings)
        rename = await self.request("PUT", path, json={"name": "renamed"})
        self.assertEqual(rename.json()["settings"], settings)

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

    async def test_webhook_config_secrets_reference_and_duplicate_application(self):
        application = await self.stores.wecom.create_application(
            name="app", corp_id="corp", wecom_agent_id="1", app_secret="fixture-secret")
        payload = {"name": "app channel", "channel_type": "wecom_app", "transport": "webhook",
                   "agent_id": self.agent_id, "session_user_id": "owner", "webhook_token": "Token123",
                   "webhook_aes_key": "A" * 43, "settings": {"wecom_app": {"application_id": application.id}}}
        response = await self.request("POST", "/api/v1/channel-accounts", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        data = response.json()
        self.assertIsNone(data["bot_id"])
        self.assertTrue(data["webhook_token_set"])
        self.assertNotIn('"webhook_token":', response.text)
        self.assertNotIn('"webhook_aes_key":', response.text)
        self.assertEqual(data["webhook_url"], "/api/v1/webhooks/channels/" + data["id"])
        path = "/api/v1/channel-accounts/" + data["id"]
        response = await self.request("PUT", path, json={"webhook_token": "", "webhook_aes_key": ""})
        self.assertEqual(response.status_code, 200, response.text)
        row = await self.stores.channel.get_account(data["id"])
        self.assertEqual(row.webhook_token, "Token123")
        response = await self.request("PUT", path, json={"webhook_token": None})
        self.assertEqual(response.status_code, 422)
        response = await self.request("PUT", path, json={"webhook_token": data["webhook_token_masked"]})
        self.assertEqual(response.status_code, 409)
        response = await self.request("POST", "/api/v1/channel-accounts", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(await self.stores.wecom.application_channel_count(application.id), 2)
        with self.assertRaises(ValueError):
            await self.stores.wecom.delete_application(application.id)
        payload["settings"]["wecom_app"]["application_id"] = "missing"
        response = await self.request("POST", "/api/v1/channel-accounts", json=payload)
        self.assertEqual(response.status_code, 409, response.text)


if __name__ == "__main__":
    unittest.main()
