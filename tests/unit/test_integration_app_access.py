import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from starlette.requests import Request

from deep_agent.services.integration_app_access import (
    IntegrationAppAccessError,
    IntegrationAppDirectory,
    managed_tenants,
)
from deep_agent.services.scope_names import ScopeDirectory


def request(headers=()):
    return Request({"type": "http", "headers": [(k.encode(), v.encode()) for k, v in headers]})


def account():
    return {
        "userId": "operator",
        "enterpriseDTOList": [
            {"enterpriseCode": "managed", "enterpriseName": "预算企业",
             "roleList": [{"roleCode": "DEFAULT_ADMIN"}]},
            {"enterpriseCode": "other", "roleList": [{"roleCode": "DEFAULT_USER"}]},
            {"enterpriseCode": "incomplete", "roleList": None},
        ],
    }


class IntegrationAppAccessTests(unittest.IsolatedAsyncioTestCase):
    def test_enterprise_scope_is_verified_per_role_and_account(self):
        self.assertEqual(managed_tenants(account(), "operator"), {"managed": "预算企业"})
        with self.assertRaises(IntegrationAppAccessError) as caught:
            managed_tenants(account(), "another-user")
        self.assertEqual(caught.exception.status, 503)

    async def test_multiple_apps_share_platform_reads_and_require_active_deepfos_app(self):
        directory = IntegrationAppDirectory(request((("cookie", "test-cookie"),)), "operator")

        async def platform(_service, path, *, space=None, app=None, params=None):
            if path == "api/enterprise/list":
                return [{"id": "tenant-pk", "enterpriseCode": "managed", "enterpriseName": "预算企业"}]
            if path == "space/get-tenant-code":
                return "managed" if space == "budget" else "other"
            if path == "user/permission":
                self.assertEqual(space, "budget")
                self.assertIn(app, {"budget001", "budget002"})
                return {"appAdmin": False, "developerRole": True}
            return [
                {"id": "budget001", "appType": "DEEPFOS", "appName": "年度预算"},
                {"id": "budget002", "appType": "DEEPFOS", "appName": "月报"},
                {"id": "budget003", "appType": "THIRD_PARTY"},
            ]

        directory._get = AsyncMock(side_effect=platform)
        first, second = await asyncio.gather(directory.verify("budget001"), directory.verify("budget002"))
        self.assertEqual((first.space_id, first.tenant_id, first.app_name), ("budget", "managed", "年度预算"))
        self.assertEqual(second.app_name, "月报")
        self.assertEqual(directory._get.await_count, 5)
        for app in ("budget003", "budget004", "another001"):
            with self.subTest(app=app), self.assertRaises(IntegrationAppAccessError) as caught:
                await directory.verify(app)
            self.assertEqual(caught.exception.status, 404)
        self.assertEqual(directory._get.await_count, 6)

    async def test_app_scoped_key_cannot_check_a_different_scope(self):
        directory = IntegrationAppDirectory(request((
            ("x-deepfos-key", "test-key"), ("app", "budget001"), ("space", "budget"),
        )), "operator")
        directory._get = AsyncMock()
        with self.assertRaises(IntegrationAppAccessError) as caught:
            await directory.verify("budget002")
        self.assertEqual(caught.exception.status, 404)
        directory._get.assert_not_awaited()

    async def test_available_apps_respect_admin_and_credential_scopes(self):
        catalog = ScopeDirectory(
            apps={"budget001": "年度预算"},
            spaces={"budget": "预算空间"},
            app_scopes={
                "budget001": ("budget", "managed"),
                "budget002": ("budget", "managed"),
                "private001": ("private", "other"),
                "stale001": ("budget", "managed"),
                "invalid": ("budget", "managed"),
            },
        )
        fetch = AsyncMock(return_value=catalog)
        with patch("deep_agent.services.integration_app_access.PlatformScopeDirectoryFetcher", return_value=fetch):
            directory = IntegrationAppDirectory(request((("cookie", "test-cookie"),)), "operator")
            async def platform(_service, path, *, space=None, app=None, params=None):
                if path == "api/enterprise/list":
                    return [
                        {"id": "tenant-pk-1", "enterpriseCode": "managed", "enterpriseName": "预算企业"},
                        {"id": "tenant-pk-2", "enterpriseCode": "other"},
                    ]
                if path == "user/permission":
                    self.assertEqual((space, app), ("budget", "budget001"))
                    return {"appAdmin": False, "developerRole": False}
                self.assertIsNone(space)
                self.assertIsNone(app)
                return {"permission": params["enterpriseId"] == "tenant-pk-1", "spaceIds": [], "appIds": []}

            directory._get = AsyncMock(side_effect=platform)
            apps = await directory.available_apps()
            self.assertEqual({app.app_id for app in apps}, {"budget001", "budget002"})
            named = next(app for app in apps if app.app_id == "budget001")
            self.assertEqual((named.app_name, named.space_name, named.tenant_name),
                             ("年度预算", "预算空间", "预算企业"))
            self.assertEqual(directory._get.await_count, 3)
            fetch.assert_awaited_once()

            scoped = IntegrationAppDirectory(request((
                ("x-deepfos-key", "test-key"), ("app", "budget001"), ("space", "budget"),
            )), "operator")
            scoped._get = AsyncMock(side_effect=platform)
            self.assertEqual(await scoped.available_apps(), [])
            self.assertEqual([call.args[1] for call in scoped._get.await_args_list],
                             ["api/enterprise/list", "user/permission"])

    async def test_login_context_does_not_seed_bulk_permissions(self):
        directory = IntegrationAppDirectory(request((
            ("cookie", "test-cookie"), ("app", "budget001"), ("space", "budget"),
        )), "operator")
        self.assertNotIn("app", directory._headers)
        self.assertNotIn("space", directory._headers)
