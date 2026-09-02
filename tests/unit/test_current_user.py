from __future__ import annotations

import unittest
from collections.abc import Mapping
from unittest.mock import AsyncMock, Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deep_agent.api.v1.current_user import (
    get_current_user_profile_resolver,
    router,
)
from deep_agent.local_dev import LocalIdentity, LocalIdentityAdapter
from deep_agent.security.current_user import (
    CurrentUserProfile,
    CurrentUserProfileResolver,
    CurrentUserUnavailableError,
    PlatformCurrentUserFetcher,
)


class _FakeResponse:
    def __init__(self, status: int, text: str) -> None:
        self.status = status
        self._text = text
        self.released = False

    async def text(self) -> str:
        return self._text

    def release(self) -> None:
        self.released = True


class PlatformCurrentUserFetcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_reads_username_from_account_center_current_user_api(self) -> None:
        response = _FakeResponse(
            200,
            '{"status":true,"data":{"id":"user-1","username":"周文俊","nickname":"乳鸽"}}',
        )
        request = AsyncMock(return_value=response)
        fetcher = PlatformCurrentUserFetcher(
            account_service="https://account.test/system-account-api",
        )

        with patch(
            "deep_agent.security.current_user.AioHttpCli.get",
            new=request,
        ):
            profile = await fetcher(
                "user-1",
                {"authorization": "Bearer token", "user": "user-1"},
            )

        self.assertEqual(
            profile,
            CurrentUserProfile(user_id="user-1", username="周文俊"),
        )
        self.assertTrue(response.released)
        request.assert_awaited_once()
        self.assertEqual(
            request.await_args.args,
            ("https://account.test/system-account-api/api/user/full-detail",),
        )
        self.assertEqual(
            request.await_args.kwargs["headers"],
            {"authorization": "Bearer token", "user": "user-1"},
        )

    async def test_rejects_missing_username_or_mismatched_user(self) -> None:
        fetcher = PlatformCurrentUserFetcher(account_service="https://account.test")
        for body in (
            '{"status":true,"data":{"id":"user-1"}}',
            '{"status":true,"data":{"id":"other","username":"Other"}}',
            '{"status":true,"data":{"username":"Missing id"}}',
            '{"status":false,"data":{"id":"user-1","username":"Rejected"}}',
            '{"status":true,"data":"invalid"}',
        ):
            with self.subTest(body=body), patch(
                "deep_agent.security.current_user.AioHttpCli.get",
                new=AsyncMock(return_value=_FakeResponse(200, body)),
            ):
                with self.assertRaises(CurrentUserUnavailableError):
                    await fetcher("user-1", {"user": "user-1"})


class CurrentUserApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fetcher = AsyncMock(
            return_value=CurrentUserProfile(user_id="user-1", username="周文俊")
        )
        self.resolver = CurrentUserProfileResolver(self.fetcher)
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_current_user_profile_resolver] = (
            lambda: self.resolver
        )
        self.client = TestClient(
            app,
            headers={"user": "user-1", "authorization": "Bearer token"},
        )

    def test_returns_only_current_user_id_and_username(self) -> None:
        response = self.client.get("/api/v1/current-user")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            response.json(),
            {"user_id": "user-1", "username": "周文俊"},
        )
        self.fetcher.assert_awaited_once()
        user_id, headers = self.fetcher.await_args.args
        self.assertEqual(user_id, "user-1")
        self.assertEqual(headers["authorization"], "Bearer token")
        self.assertEqual(headers["user"], "user-1")

    def test_local_adapter_uses_same_api_contract_without_account_call(self) -> None:
        app = FastAPI()
        app.include_router(router, prefix="/api/v1")
        app.dependency_overrides[get_current_user_profile_resolver] = (
            lambda: self.resolver
        )
        local_app = LocalIdentityAdapter(
            app,
            LocalIdentity(
                user="local-user-id",
                username="本地测试用户",
                console_admin_bypass=True,
            ),
        )

        with TestClient(local_app) as client:
            response = client.get("/api/v1/current-user")

        self.assertEqual(
            response.json(),
            {"user_id": "local-user-id", "username": "本地测试用户"},
        )
        self.fetcher.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
