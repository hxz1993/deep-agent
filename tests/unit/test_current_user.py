from __future__ import annotations

import asyncio
import json
import unittest
from collections.abc import Mapping
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from deep_agent.agent.headers import extract_forward_headers
from deep_agent.options import DEFAULT_MCP_FORWARD_HEADERS
from deep_agent.api.v1.current_user import (
    get_current_user_profile_resolver,
    router,
)
from deep_agent.local_dev import LocalIdentity, LocalIdentityAdapter
from deep_agent.security.current_user import (
    CurrentUserAuthenticationError,
    CurrentUserProfile,
    CurrentUserProfileResolver,
    CurrentUserUnavailableError,
    PlatformCurrentUserFetcher,
    get_current_user_profile_resolver as shared_profile_resolver,
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
            CurrentUserProfile(user_id="user-1", username="周文俊", nickname="乳鸽"),
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

    async def test_projects_profile_fields_and_rejects_sensitive_avatar_url(self) -> None:
        fetcher = PlatformCurrentUserFetcher(account_service="https://account.test")
        for avatar, expected in (("/avatars/user.png", "/avatars/user.png"),
                                 ("https://cdn.test/avatar?token=secret", None)):
            payload = {"id": 4, "userName": " account ", "nickName": " 昵称 ",
                       "email": " user@example.test ", "avatar": avatar,
                       "mobilePhone": "private", "password": "private"}
            with self.subTest(avatar=avatar), patch(
                "deep_agent.security.current_user.AioHttpCli.get",
                new=AsyncMock(return_value=_FakeResponse(200, json.dumps({"data": payload}))),
            ):
                profile = await fetcher("4", {"user": "4"})
            self.assertEqual(profile, CurrentUserProfile("4", "account", "昵称", "user@example.test", expected))

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

    async def test_empty_mode_accepts_successful_empty_fields_without_inventing_names(self) -> None:
        fetcher = PlatformCurrentUserFetcher(account_service="https://account.test", allow_empty=True)
        for payload in ({}, {"data": {}}, {"status": True, "data": {
            "id": "user-1", "username": "", "nickname": None, "avatar": None,
        }}):
            with self.subTest(payload=payload), patch(
                "deep_agent.security.current_user.AioHttpCli.get",
                new=AsyncMock(return_value=_FakeResponse(200, json.dumps(payload))),
            ):
                profile = await fetcher("user-1", {"user": "user-1"})
            self.assertEqual(profile, CurrentUserProfile("user-1", "", resolution="empty"))

    async def test_empty_mode_preserves_explicit_null_and_empty_string_fields(self) -> None:
        fetcher = PlatformCurrentUserFetcher(account_service="https://account.test", allow_empty=True)
        for username, nickname, email in ((None, "", None), ("", None, "")):
            with self.subTest(username=username), patch(
                "deep_agent.security.current_user.AioHttpCli.get", new=AsyncMock(
                    return_value=_FakeResponse(200, json.dumps({"data": {
                        "id": " ", "userId": None, "username": username,
                        "nickname": nickname, "email": email,
                    }})),
                ),
            ):
                profile = await fetcher("user-1", {})
            self.assertEqual(profile, CurrentUserProfile(
                "user-1", username, nickname, email, resolution="empty",
            ))

    async def test_public_text_fields_are_bounded(self) -> None:
        fetcher = PlatformCurrentUserFetcher(account_service="https://account.test", allow_empty=True)
        with patch("deep_agent.security.current_user.AioHttpCli.get", new=AsyncMock(
            return_value=_FakeResponse(200, json.dumps({"data": {
                "id": "user-1", "username": "u" * 10000,
                "nickname": "n" * 10000, "email": "e" * 10000,
                "avatar": "https://cdn.test/" + "a" * 10000,
            }})),
        )):
            profile = await fetcher("user-1", {})
        self.assertEqual(len(profile.username), 256)
        self.assertEqual(len(profile.nickname), 256)
        self.assertEqual(len(profile.email), 320)
        self.assertIsNone(profile.avatar_url)

    async def test_empty_mode_preserves_partial_public_profile(self) -> None:
        payload = {"id": "user-1", "username": None, "nickname": "昵称",
                   "email": "user@example.test", "avatar": "/avatars/user.png",
                   "password": "private", "mobilePhone": "private"}
        fetcher = PlatformCurrentUserFetcher(account_service="https://account.test", allow_empty=True)
        with patch("deep_agent.security.current_user.AioHttpCli.get", new=AsyncMock(
            return_value=_FakeResponse(200, json.dumps({"data": payload})),
        )):
            profile = await fetcher("user-1", {"user": "user-1"})
        self.assertEqual(profile, CurrentUserProfile(
            "user-1", None, "昵称", "user@example.test", "/avatars/user.png",
        ))
        self.assertEqual(set(vars(profile)), {
            "user_id", "username", "nickname", "email", "avatar_url", "resolution",
        })

    async def test_empty_mode_rejects_unattributed_or_mismatched_profiles(self) -> None:
        fetcher = PlatformCurrentUserFetcher(account_service="https://account.test", allow_empty=True)
        for payload in (
            {"id": "other"}, {"username": "Other"}, {"nickname": "Other"},
            {"email": "other@example.test"}, {"avatar": "https://cdn.test/avatar?token=private"},
            {"id": "user-1", "userId": "other"},
        ):
            with self.subTest(payload=payload), patch(
                "deep_agent.security.current_user.AioHttpCli.get", new=AsyncMock(
                    return_value=_FakeResponse(200, json.dumps({"data": payload})),
                ),
            ):
                with self.assertRaises(CurrentUserUnavailableError):
                    await fetcher("user-1", {"user": "user-1"})

    async def test_empty_mode_still_rejects_failed_or_malformed_responses(self) -> None:
        fetcher = PlatformCurrentUserFetcher(account_service="https://account.test", allow_empty=True)
        for payload in ({"status": False}, {"status": False, "data": {}},
                        {"data": None}, {"data": []}, []):
            with self.subTest(payload=payload), patch(
                "deep_agent.security.current_user.AioHttpCli.get", new=AsyncMock(
                    return_value=_FakeResponse(200, json.dumps(payload)),
                ),
            ):
                with self.assertRaises(CurrentUserUnavailableError):
                    await fetcher("user-1", {"user": "user-1"})

    async def test_non_text_username_is_not_serialized_into_public_profile(self) -> None:
        fetcher = PlatformCurrentUserFetcher(account_service="https://account.test", allow_empty=True)
        with patch("deep_agent.security.current_user.AioHttpCli.get", new=AsyncMock(
            return_value=_FakeResponse(200, json.dumps({
                "data": {"id": "user-1", "username": {"password": "private"}},
            })),
        )):
            profile = await fetcher("user-1", {"user": "user-1"})
        self.assertEqual(profile, CurrentUserProfile("user-1", "", resolution="empty"))


class CurrentUserProfileResolverTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def request(headers: Mapping[str, str]) -> Request:
        return Request({"type": "http", "headers": [
            (key.encode("ascii"), value.encode("latin-1")) for key, value in headers.items()
        ]})

    async def test_console_and_execution_use_same_default_resolver(self) -> None:
        self.assertIs(get_current_user_profile_resolver(), shared_profile_resolver())

    async def test_real_console_headers_and_chat_extraction_share_effective_language(self) -> None:
        for override, canonical, expected in (
            ("zh-cn", "en-us", "zh-cn"),
            (None, "en-us", "en-us"),
            (None, None, "zh-cn"),
        ):
            with self.subTest(override=override, canonical=canonical):
                fetcher = AsyncMock(return_value=CurrentUserProfile("user-1", "Account"))
                resolver = CurrentUserProfileResolver(fetcher)
                raw = {"authorization": "Bearer browser-token", "user": "user-1",
                       "accept-language": "en-US,en;q=0.9,zh-CN;q=0.8"}
                if override is not None:
                    raw["x-deep-agent-language"] = override
                if canonical is not None:
                    raw["language"] = canonical
                browser_request = self.request(raw)
                first = await resolver.profile_for(browser_request, "user-1")
                second = await resolver.profile_for_headers(
                    "user-1", extract_forward_headers(browser_request, DEFAULT_MCP_FORWARD_HEADERS),
                    allow_empty=True,
                )
                self.assertIs(first, second)
                fetcher.assert_awaited_once()
                self.assertEqual(fetcher.await_args.args[1], {
                    "authorization": "Bearer browser-token", "user": "user-1",
                    "language": expected,
                })
    async def test_console_warmup_and_execution_headers_reuse_actual_account_request(self) -> None:
        resolver = CurrentUserProfileResolver(PlatformCurrentUserFetcher(
            account_service="https://account.test", allow_empty=True,
        ))
        request = AsyncMock(return_value=_FakeResponse(
            200, '{"data":{"id":"user-1","username":"Account"}}',
        ))
        credentials = {"authorization": "Bearer test-token", "cookie": "test-cookie",
                       "language": "zh-cn", "accept-language": "zh-CN"}
        console_headers = {**credentials, "app": "app001", "space": "app",
                           "x-request-id": "console-trace"}
        with patch("deep_agent.security.current_user.AioHttpCli.get", new=request):
            first = await resolver.profile_for(self.request(console_headers), "user-1")
            second = await resolver.profile_for_headers("user-1", {
                **credentials, "user": "user-1", "app": "other001", "space": "other",
                "trace": "execution-one", "x-request-id": "execution-trace",
                "x-forwarded-for": "127.0.0.1", "x-channel-id": "channel-one",
            }, allow_empty=True)
            third = await resolver.profile_for_headers("user-1", {
                **credentials, "app": "third001", "space": "third",
                "trace": "execution-two", "x-request-id": "another-trace",
            }, allow_empty=True)
        self.assertIs(first, second)
        self.assertIs(second, third)
        request.assert_awaited_once()
        self.assertEqual(request.await_args.kwargs["headers"], {
            "authorization": "Bearer test-token", "cookie": "test-cookie",
            "language": "zh-cn", "user": "user-1",
        })

    async def test_request_and_trusted_headers_reuse_successful_profile(self) -> None:
        fetcher = AsyncMock(return_value=CurrentUserProfile("user-1", "Account"))
        resolver = CurrentUserProfileResolver(fetcher)
        headers = {"authorization": "Bearer test-token", "language": "zh-cn"}
        first = await resolver.profile_for(self.request(headers), "user-1")
        second = await resolver.profile_for_headers(" user-1 ", {
            "Authorization": "Bearer test-token", "Language": "zh-cn", "user": "untrusted",
        }, allow_empty=True)
        self.assertIs(first, second)
        fetcher.assert_awaited_once()
        self.assertEqual(fetcher.await_args.args[1]["user"], "user-1")
        self.assertEqual(fetcher.await_args.args[1]["language"], "zh-cn")

    async def test_cache_isolates_user_credentials_language_and_tenant(self) -> None:
        fetcher = AsyncMock(side_effect=lambda user, _: CurrentUserProfile(user, "Account"))
        resolver = CurrentUserProfileResolver(fetcher)
        base = {"authorization": "Bearer one", "language": "zh-cn"}
        variants = [
            ("user-1", base), ("user-2", base),
            ("user-1", {**base, "authorization": "Bearer two"}),
            ("user-1", {**base, "language": "en-us"}),
            ("user-1", {**base, "x-tenant-id": "tenant-two"}),
        ]
        for user, headers in variants * 2:
            await resolver.profile_for_headers(user, headers)
        self.assertEqual(fetcher.await_count, len(variants))
        self.assertNotIn("Bearer", repr(resolver._cache))

    async def test_api_key_scope_remains_forwarded_and_separates_cache_entries(self) -> None:
        fetcher = AsyncMock(return_value=CurrentUserProfile("user-1", "Account"))
        resolver = CurrentUserProfileResolver(fetcher)
        for app, space in (("app001", "app"), ("app002", "app"), ("app001", "other")):
            headers = {"x-deepfos-key": "test-key", "app": app, "space": space}
            await resolver.profile_for(self.request(headers), "user-1")
            await resolver.profile_for_headers("user-1", {**headers, "trace": "another-run"})
        self.assertEqual(fetcher.await_count, 3)
        self.assertEqual(fetcher.await_args.args[1], {
            "x-deepfos-key": "test-key", "app": "app001", "space": "other", "user": "user-1",
            "language": "zh-cn",
        })

    async def test_trusted_headers_keep_console_api_key_authentication_checks(self) -> None:
        fetcher = AsyncMock()
        resolver = CurrentUserProfileResolver(fetcher)
        for headers in (
            {"x-deepfos-key": "test-key"},
            {"x-deepfos-key": "test-key", "app": "app001", "space": "invalid/space"},
            {"x-deepfos-key": "test-key", "app": "app001", "space": "app",
             "authorization": "Bearer test-token"},
            {"x-deepfos-key": "test-key", "app": "app001", "space": "app",
             "cookie": "test-cookie"},
        ):
            with self.subTest(headers=headers), self.assertRaises(CurrentUserAuthenticationError):
                await resolver.profile_for_headers("user-1", headers)
        fetcher.assert_not_awaited()

    async def test_cache_namespace_tracks_fetchers_actual_account_service(self) -> None:
        fetcher = PlatformCurrentUserFetcher(account_service="https://account-one.test")
        resolver = CurrentUserProfileResolver(fetcher)
        request = AsyncMock(side_effect=[
            _FakeResponse(200, '{"data":{"id":"user-1","username":"One"}}'),
            _FakeResponse(200, '{"data":{"id":"user-1","username":"Two"}}'),
        ])
        with patch("deep_agent.security.current_user.AioHttpCli.get", new=request):
            first = await resolver.profile_for_headers("user-1", {})
            fetcher._account_service = "https://account-two.test"
            second = await resolver.profile_for_headers("user-1", {})
        self.assertEqual((first.username, second.username), ("One", "Two"))
        self.assertEqual(request.await_count, 2)
        self.assertEqual(request.await_args.args[0], "https://account-two.test/api/user/full-detail")

    async def test_success_cache_expires_thirty_seconds_after_completion(self) -> None:
        now = [0.0]

        async def fetch(user: str, _: Mapping[str, str]) -> CurrentUserProfile:
            now[0] += 4
            return CurrentUserProfile(user, "Account")

        fetcher = AsyncMock(side_effect=fetch)
        resolver = CurrentUserProfileResolver(fetcher, clock=lambda: now[0])
        await resolver.profile_for_headers("user-1", {})
        now[0] = 33.0
        await resolver.profile_for_headers("user-1", {})
        fetcher.assert_awaited_once()
        now[0] = 34.0
        await resolver.profile_for_headers("user-1", {})
        self.assertEqual(fetcher.await_count, 2)

    async def test_success_cache_remains_bounded_at_256_profiles(self) -> None:
        fetcher = AsyncMock(side_effect=lambda user, _: CurrentUserProfile(user, "Account"))
        resolver = CurrentUserProfileResolver(fetcher)
        for index in range(257):
            await resolver.profile_for_headers(f"user-{index}", {})
        self.assertEqual(len(resolver._cache), 256)
        await resolver.profile_for_headers("user-256", {})
        self.assertEqual(fetcher.await_count, 257)
        await resolver.profile_for_headers("user-0", {})
        self.assertEqual(fetcher.await_count, 258)

    async def test_empty_success_is_available_to_execution_and_console_stays_strict(self) -> None:
        resolver = CurrentUserProfileResolver()
        request = AsyncMock(return_value=_FakeResponse(200, '{"status":true,"data":{}}'))
        with patch("deep_agent.security.current_user.AioHttpCli.get", new=request), patch(
            "deep_agent.security.current_user.PlatformCurrentUserFetcher._resolve_url",
            new=AsyncMock(return_value=("https://account.test/api/user/full-detail", None, None)),
        ):
            profile = await resolver.profile_for_headers("user-1", {}, allow_empty=True)
            with self.assertRaises(CurrentUserUnavailableError):
                await resolver.profile_for(self.request({}), "user-1")
        self.assertEqual(profile, CurrentUserProfile("user-1", "", resolution="empty"))
        request.assert_awaited_once()

    async def test_console_preserves_previous_empty_optional_field_normalization(self) -> None:
        fetcher = AsyncMock(return_value=CurrentUserProfile("user-1", "Account", "", ""))
        resolver = CurrentUserProfileResolver(fetcher)
        execution = await resolver.profile_for_headers("user-1", {}, allow_empty=True)
        console = await resolver.profile_for(self.request({}), "user-1")
        self.assertEqual((execution.nickname, execution.email), ("", ""))
        self.assertEqual((console.nickname, console.email), (None, None))
        fetcher.assert_awaited_once()

    async def test_concurrent_waiters_share_one_request_and_cancellation_is_isolated(self) -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def fetch(user: str, _: Mapping[str, str]) -> CurrentUserProfile:
            started.set()
            await release.wait()
            return CurrentUserProfile(user, "Account")

        fetcher = AsyncMock(side_effect=fetch)
        resolver = CurrentUserProfileResolver(fetcher)
        first = asyncio.create_task(resolver.profile_for_headers("user-1", {}))
        await started.wait()
        others = [asyncio.create_task(resolver.profile_for_headers("user-1", {})) for _ in range(8)]
        first.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await first
        release.set()
        profiles = await asyncio.gather(*others)
        self.assertTrue(all(profile is profiles[0] for profile in profiles))
        fetcher.assert_awaited_once()

    async def test_abandoned_shared_lookup_times_out_during_service_discovery(self) -> None:
        started, cancelled = asyncio.Event(), asyncio.Event()

        async def resolve_url():
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()

        fetcher = PlatformCurrentUserFetcher(account_service="https://account.test", allow_empty=True)
        resolver = CurrentUserProfileResolver(fetcher)
        with patch("deep_agent.security.current_user._PROFILE_TIMEOUT_SECONDS", 0.01), patch.object(
            fetcher, "_resolve_url", new=AsyncMock(side_effect=resolve_url),
        ), patch("deep_agent.security.current_user.AioHttpCli.get", new=AsyncMock()) as request:
            waiter = asyncio.create_task(resolver.profile_for_headers("user-1", {}))
            await started.wait()
            waiter.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await waiter
            self.assertFalse(cancelled.is_set())
            await asyncio.wait_for(cancelled.wait(), timeout=1.0)
            await asyncio.sleep(0)
        request.assert_not_awaited()
        self.assertFalse(resolver._inflight)
        self.assertFalse(resolver._cache)

    async def test_shared_lookup_total_timeout_is_unavailable_and_not_cached(self) -> None:
        async def blocked_fetch(*_):
            await asyncio.Event().wait()

        fetcher = AsyncMock(side_effect=blocked_fetch)
        resolver = CurrentUserProfileResolver(fetcher)
        with patch("deep_agent.security.current_user._PROFILE_TIMEOUT_SECONDS", 0.01):
            for _ in range(2):
                with self.assertRaises(CurrentUserUnavailableError):
                    await resolver.profile_for_headers("user-1", {})
        self.assertEqual(fetcher.await_count, 2)
        self.assertFalse(resolver._inflight)
        self.assertFalse(resolver._cache)

    async def test_failures_are_shared_inflight_then_retried_without_success_cache(self) -> None:
        started, release = asyncio.Event(), asyncio.Event()

        async def fetch(_: str, __: Mapping[str, str]) -> CurrentUserProfile:
            started.set()
            await release.wait()
            raise CurrentUserUnavailableError("unavailable")

        fetcher = AsyncMock(side_effect=fetch)
        resolver = CurrentUserProfileResolver(fetcher)
        tasks = [asyncio.create_task(resolver.profile_for_headers("user-1", {})) for _ in range(4)]
        await started.wait()
        release.set()
        errors = await asyncio.gather(*tasks, return_exceptions=True)
        self.assertTrue(all(isinstance(error, CurrentUserUnavailableError) for error in errors))
        fetcher.assert_awaited_once()
        self.assertEqual(resolver._cache, {})
        with self.assertRaises(CurrentUserUnavailableError):
            await resolver.profile_for_headers("user-1", {})
        self.assertEqual(fetcher.await_count, 2)

    async def test_mismatched_custom_profile_is_rejected_and_not_cached(self) -> None:
        fetcher = AsyncMock(return_value=CurrentUserProfile("other", "Other"))
        resolver = CurrentUserProfileResolver(fetcher)
        for _ in range(2):
            with self.assertRaises(CurrentUserUnavailableError):
                await resolver.profile_for_headers("user-1", {}, allow_empty=True)
        self.assertEqual(fetcher.await_count, 2)
        self.assertEqual(resolver._cache, {})

    async def test_trusted_local_username_returns_without_account_call(self) -> None:
        fetcher = AsyncMock()
        resolver = CurrentUserProfileResolver(fetcher)
        self.assertEqual(await resolver.profile_for_headers(
            "user-1", {}, local_username=" 本地用户 ", allow_empty=True,
        ), CurrentUserProfile("user-1", "本地用户", resolution="local"))
        fetcher.assert_not_awaited()
        with self.assertRaises(CurrentUserAuthenticationError):
            await resolver.profile_for_headers(" ", {}, local_username="本地用户")

    async def test_invalid_header_values_or_duplicate_names_are_rejected(self) -> None:
        fetcher = AsyncMock()
        resolver = CurrentUserProfileResolver(fetcher)
        for headers in ({"language": "zh-cn\r\nprivate"},
                        {"Authorization": "one", "authorization": "two"}):
            with self.subTest(headers=headers), self.assertRaises(CurrentUserAuthenticationError):
                await resolver.profile_for_headers("user-1", headers)
        fetcher.assert_not_awaited()


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
        self.client = self.enterContext(TestClient(
            app,
            headers={"user": "user-1", "authorization": "Bearer token"},
        ))

    def test_returns_only_safe_current_user_profile_fields(self) -> None:
        response = self.client.get("/api/v1/current-user")

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(
            response.json(),
            {"user_id": "user-1", "username": "周文俊", "display_name": "周文俊",
             "nickname": None, "email": None, "avatar_url": None, "resolution": "resolved"},
        )
        self.fetcher.assert_awaited_once()
        user_id, headers = self.fetcher.await_args.args
        self.assertEqual(user_id, "user-1")
        self.assertEqual(headers["authorization"], "Bearer token")
        self.assertEqual(headers["user"], "user-1")

    def test_console_api_warms_account_cache_for_execution_headers(self) -> None:
        self.resolver._fetcher = PlatformCurrentUserFetcher(
            account_service="https://account.test", allow_empty=True,
        )
        request = AsyncMock(return_value=_FakeResponse(
            200, '{"data":{"id":"user-1","username":"Account"}}',
        ))
        with patch("deep_agent.security.current_user.AioHttpCli.get", new=request):
            response = self.client.get("/api/v1/current-user", headers={
                "app": "app001", "space": "app", "language": "zh-cn",
                "trace": "console-trace",
            })
            profile = asyncio.run(self.resolver.profile_for_headers("user-1", {
                "authorization": "Bearer token", "app": "other001", "space": "other",
                "language": "zh-cn", "trace": "run-trace", "user": "user-1",
                "x-request-id": "execution-trace", "x-channel-id": "channel-one",
            }, allow_empty=True))
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(profile.username, response.json()["username"])
        request.assert_awaited_once()
        self.assertEqual(request.await_args.kwargs["headers"], {
            "authorization": "Bearer token", "user": "user-1", "language": "zh-cn",
        })

    def test_account_errors_and_missing_identity_are_not_cached(self) -> None:
        for error, expected in (
            (CurrentUserAuthenticationError("expired"), 401),
            (CurrentUserUnavailableError("unavailable"), 503),
        ):
            with self.subTest(status=expected):
                self.fetcher.side_effect = error
                response = self.client.get("/api/v1/current-user")
                self.assertEqual(response.status_code, expected)
                self.assertEqual(response.headers["cache-control"], "no-store")
        response = self.client.get("/api/v1/current-user", headers={"user": ""})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.headers["cache-control"], "no-store")

    def test_empty_profile_keeps_existing_503_contract(self) -> None:
        self.fetcher.return_value = CurrentUserProfile("user-1", "", resolution="empty")
        response = self.client.get("/api/v1/current-user")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.headers["cache-control"], "no-store")
        self.assertEqual(response.json(), {"detail": "暂时无法读取当前用户名，请稍后重试"})

    def test_invalid_console_api_key_credentials_keep_401_contract(self) -> None:
        response = self.client.get("/api/v1/current-user", headers={"x-deepfos-key": "test-key"})
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json(), {"detail": "平台登录状态已失效，请重新登录"})
        self.fetcher.assert_not_awaited()

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
            {"user_id": "local-user-id", "username": "本地测试用户", "display_name": "本地测试用户",
             "nickname": None, "email": None, "avatar_url": None, "resolution": "local"},
        )
        self.fetcher.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
