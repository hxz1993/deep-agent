from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from fastapi import Request

from deep_agent.local_security import (
    LOCAL_CONSOLE_ADMIN_SCOPE_KEY,
    LOCAL_CURRENT_USERNAME_SCOPE_KEY,
)
from deep_agent.security.diagnostic_users import (
    DiagnosticUserAuthenticationError,
    DiagnosticUserProfileResolver,
    DiagnosticUserUnavailableError,
    PlatformDiagnosticUserFetcher,
    safe_avatar_url,
)


def request(*, token="test-token", host="deployment.example", **scope):
    return Request({
        "type": "http", "scheme": "https", "path": "/api/v1/diagnostics/sessions",
        "root_path": "", "query_string": b"", "server": (host, 443),
        "headers": [(b"host", host.encode()), *([(b"authorization", f"Bearer {token}".encode())] if token else []),
                    (b"user", b"forged-target"), (b"app", b"ignored-app"),
                    (b"space", b"ignored-space"), (b"x-tenant-id", b"tenant-1")],
        **scope,
    })


def profile(user_id, name="Display name"):
    return {
        "id": user_id, "user_id": user_id, "display_name": name,
        "email": "display@example.test", "avatar_url": None, "resolution": "resolved",
    }


class Response:
    def __init__(self, status=200, payload=None, *, body=None):
        self.status = status
        self._body = body if body is not None else json.dumps(payload).encode()
        self.released = False
        self.content = self

    async def iter_chunked(self, size):
        for offset in range(0, len(self._body), size):
            yield self._body[offset:offset + size]

    def release(self):
        self.released = True


class PlatformDiagnosticUserFetcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_numeric_legacy_ids_and_username_priority(self):
        response = Response(payload={"status": True, "data": [
            {"userId": 0, "userName": "zero"},
            {"userId": 4, "userName": "four", "nickName": "四号昵称"},
            {"userId": "5", "username": "five", "nickname": "五号昵称"},
            {"userId": "no-name", "nickName": "仅有昵称"},
            {"userId": True, "userName": "invalid boolean id"},
        ]})
        with patch("deep_agent.security.diagnostic_users.AioHttpCli.post", AsyncMock(return_value=response)):
            result = await PlatformDiagnosticUserFetcher(space_service="https://platform.example")(
                ("0", "4", "5", "no-name", "True"), {"user": "admin"},
            )
        self.assertEqual(result["0"]["display_name"], "zero")
        self.assertEqual(result["4"]["display_name"], "four")
        self.assertEqual(result["5"]["display_name"], "five")
        self.assertEqual(result["no-name"]["display_name"], "仅有昵称")
        self.assertNotIn("True", result)

    async def test_batch_account_contract_and_minimal_profile_projection(self):
        response = Response(payload={"status": True, "data": [
            {"userId": "owner-1", "nickName": "昵称", "userName": "username",
             "email": "one@example.test", "avatar": "/avatars/one.png",
             "mobilePhone": "not-returned", "password": "not-returned"},
            {"id": "owner-2", "username": "old user", "nickname": ""},
            {"userId": "owner-3"},
            {"userId": "unrequested", "userName": "not-returned"},
        ]})
        post = AsyncMock(return_value=response)
        fetcher = PlatformDiagnosticUserFetcher(space_service="https://platform.example/deepfos-server/space-server")
        with patch("deep_agent.security.diagnostic_users.AioHttpCli.post", post):
            result = await fetcher(("owner-1", "owner-2", "owner-3"), {"user": "admin-caller"})
        self.assertEqual(set(result), {"owner-1", "owner-2", "owner-3"})
        self.assertEqual(result["owner-1"]["display_name"], "username")
        self.assertEqual(result["owner-2"]["display_name"], "old user")
        self.assertEqual(result["owner-3"]["display_name"], "owner-3")
        self.assertEqual(result["owner-1"]["avatar_url"], "/avatars/one.png")
        self.assertEqual(set(result["owner-1"]), {
            "id", "user_id", "display_name", "email", "avatar_url", "resolution",
        })
        self.assertEqual(post.await_args.args, ("https://platform.example/deepfos-server/space-server/api/space/user-info/batch/query/by-user-ids",))
        self.assertEqual(post.await_args.kwargs["json"], ["owner-1", "owner-2", "owner-3"])
        self.assertEqual(post.await_args.kwargs["headers"], {"user": "admin-caller"})
        self.assertFalse(post.await_args.kwargs["allow_redirects"])
        self.assertEqual(post.await_args.kwargs["timeout"].total, 2)
        self.assertTrue(response.released)

    async def test_uses_existing_space_service_discovery(self):
        success, failure = Mock(), Mock()
        discovery = Mock(closed=False)
        discovery.get_url = AsyncMock(return_value=(success, failure, "http://account.internal:8000"))
        response = Response(payload=[{"userId": "owner", "userName": "Account"}])
        with patch("deep_agent.security.diagnostic_users.ServiceDiscovery.instantiate", return_value=discovery), patch(
            "deep_agent.security.diagnostic_users.AioHttpCli.post", AsyncMock(return_value=response)
        ) as post:
            result = await PlatformDiagnosticUserFetcher(space_service="space-service")(("owner",), {"user": "caller"})
        discovery.get_url.assert_awaited_once_with("space-service")
        success.assert_called_once()
        failure.assert_not_called()
        self.assertEqual(post.await_args.args[0], "http://account.internal:8000/api/space/user-info/batch/query/by-user-ids")
        self.assertEqual(result["owner"]["display_name"], "Account")

    async def test_auth_errors_are_distinct_and_responses_are_released(self):
        fetcher = PlatformDiagnosticUserFetcher(space_service="https://platform.example")
        for status in (401, 403):
            response = Response(status, {"sensitive": "not-returned"})
            with self.subTest(status=status), patch(
                "deep_agent.security.diagnostic_users.AioHttpCli.post", AsyncMock(return_value=response)
            ):
                with self.assertRaises(DiagnosticUserAuthenticationError) as caught:
                    await fetcher(("owner",), {"user": "caller"})
                self.assertEqual(caught.exception.status_code, status)
                self.assertNotIn("sensitive", str(caught.exception))
                self.assertTrue(response.released)
        with patch("deep_agent.security.diagnostic_users.AioHttpCli.post", AsyncMock(
            return_value=Response(payload={"status": False, "code": "401", "data": None})
        )):
            with self.assertRaises(DiagnosticUserAuthenticationError):
                await fetcher(("owner",), {"user": "caller"})

    async def test_malformed_oversized_and_redirect_responses_never_leak(self):
        fetcher = PlatformDiagnosticUserFetcher(space_service="https://platform.example")
        for response in (
            Response(302, {}), Response(503, {}), Response(body=b"not-json"),
            Response(payload={"status": False, "data": []}),
            Response(payload={"data": {"wrong": "shape"}}),
            Response(body=b"x" * (512 * 1024 + 1)),
        ):
            with self.subTest(status=response.status), patch(
                "deep_agent.security.diagnostic_users.AioHttpCli.post", AsyncMock(return_value=response)
            ):
                with self.assertRaises(DiagnosticUserUnavailableError):
                    await fetcher(("owner",), {"user": "caller"})
                self.assertTrue(response.released)

    async def test_batch_size_and_unsafe_service_url_rejected_before_network(self):
        post = AsyncMock()
        with patch("deep_agent.security.diagnostic_users.AioHttpCli.post", post):
            for space_service in ("", "file:///tmp/account", "https://name:secret@account.example", "https://account.example?token=secret"):
                with self.subTest(space_service=space_service), self.assertRaises(DiagnosticUserUnavailableError):
                    await PlatformDiagnosticUserFetcher(space_service=space_service)(("owner",), {})
            fetcher = PlatformDiagnosticUserFetcher(space_service="https://platform.example")
            for ids in ((), tuple(str(number) for number in range(101))):
                with self.assertRaises(ValueError):
                    await fetcher(ids, {})
        post.assert_not_awaited()


class DiagnosticUserProfileResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_local_bypass_with_platform_login_still_batches_real_profiles(self):
        fetcher = AsyncMock(return_value={"local-id": profile("local-id", "platform.username")})
        local = request(**{LOCAL_CONSOLE_ADMIN_SCOPE_KEY: True, LOCAL_CURRENT_USERNAME_SCOPE_KEY: "本地用户"})
        result = await DiagnosticUserProfileResolver(fetcher).profiles_for(local, "local-id", ["local-id"])
        self.assertEqual(result["local-id"]["display_name"], "platform.username")
        fetcher.assert_awaited_once()
        self.assertEqual(fetcher.await_args.args[1]["user"], "local-id")

    async def test_local_bypass_does_not_hide_expired_platform_login(self):
        fetcher = AsyncMock(side_effect=DiagnosticUserAuthenticationError(401))
        local = request(**{LOCAL_CONSOLE_ADMIN_SCOPE_KEY: True, LOCAL_CURRENT_USERNAME_SCOPE_KEY: "本地用户"})
        with self.assertRaises(DiagnosticUserAuthenticationError):
            await DiagnosticUserProfileResolver(fetcher).profiles_for(local, "local-id", ["local-id"])

    async def test_batches_only_unique_requested_ids_using_admin_not_target_identity(self):
        async def fetch(ids, headers):
            return {user_id: profile(user_id) for user_id in ids}
        fetcher = AsyncMock(side_effect=fetch)
        resolver = DiagnosticUserProfileResolver(fetcher)
        ids = [f"owner-{index}" for index in range(205)]
        result = await resolver.profiles_for(request(), "admin-caller", [*ids, ids[0], " "])
        self.assertEqual(len(result), 205)
        self.assertEqual([len(call.args[0]) for call in fetcher.await_args_list], [100, 100, 5])
        for call in fetcher.await_args_list:
            self.assertEqual(call.args[1]["user"], "admin-caller")
            self.assertNotIn("app", call.args[1])
            self.assertNotIn("space", call.args[1])
            self.assertEqual(call.args[1]["x-tenant-id"], "tenant-1")

    async def test_cache_scoped_by_caller_login_deployment_and_has_ttl(self):
        clock = Mock(return_value=10.0)
        fetcher = AsyncMock(return_value={"owner": profile("owner")})
        resolver = DiagnosticUserProfileResolver(fetcher, clock=clock, cache_ttl_seconds=30)
        await resolver.profiles_for(request(), "caller", ["owner"])
        cached = await resolver.profiles_for(request(), "caller", ["owner"])
        cached["owner"]["display_name"] = "caller cannot mutate cache"
        result = await resolver.profiles_for(request(), "caller", ["owner"])
        self.assertEqual(result["owner"]["display_name"], "Display name")
        self.assertEqual(fetcher.await_count, 1)
        await resolver.profiles_for(request(token="second-token"), "caller", ["owner"])
        await resolver.profiles_for(request(), "other-caller", ["owner"])
        await resolver.profiles_for(request(host="other.example"), "caller", ["owner"])
        self.assertEqual(fetcher.await_count, 4)
        self.assertNotIn("test-token", repr(resolver._cache))
        clock.return_value = 41.0
        await resolver.profiles_for(request(), "caller", ["owner"])
        self.assertEqual(fetcher.await_count, 5)
        resolver.clear()
        self.assertFalse(resolver._cache)

    async def test_cache_bound_and_missing_users_are_explicit(self):
        fetcher = AsyncMock(return_value={})
        resolver = DiagnosticUserProfileResolver(fetcher, cache_max_entries=2)
        result = await resolver.profiles_for(request(), "caller", ["first", "second", "third"])
        self.assertEqual(len(resolver._cache), 2)
        self.assertEqual(result["first"]["resolution"], "missing")
        self.assertEqual(result["first"]["display_name"], "first")
        await resolver.profiles_for(request(), "caller", ["second", "third"])
        self.assertEqual(fetcher.await_count, 1)
        await resolver.profiles_for(request(), "caller", ["first"])
        self.assertEqual(fetcher.await_count, 2)

    async def test_local_bypass_only_resolves_current_platform_account(self):
        fetcher = AsyncMock(return_value={})
        resolver = DiagnosticUserProfileResolver(fetcher)
        local = request(token=None, **{LOCAL_CONSOLE_ADMIN_SCOPE_KEY: True, LOCAL_CURRENT_USERNAME_SCOPE_KEY: "本地账号"})
        result = await resolver.profiles_for(local, "local-id", ["local-id", "channel-owner"])
        self.assertEqual(result["local-id"]["display_name"], "本地账号")
        self.assertEqual(result["local-id"]["resolution"], "local")
        self.assertEqual(result["channel-owner"]["display_name"], "channel-owner")
        self.assertIsNone(result["channel-owner"]["email"])
        fetcher.assert_not_awaited()
        await resolver.profiles_for(request(**{LOCAL_CURRENT_USERNAME_SCOPE_KEY: "Not a bypass"}), "local-id", ["other"])
        fetcher.assert_awaited_once()

    async def test_missing_service_and_timeouts_degrade_without_retries(self):
        for failure in (DiagnosticUserUnavailableError("unavailable"), OSError("secret upstream text")):
            fetcher = AsyncMock(side_effect=failure)
            resolver = DiagnosticUserProfileResolver(fetcher)
            result = await resolver.profiles_for(request(), "caller", ["owner"])
            self.assertEqual(result["owner"]["resolution"], "unavailable")
            self.assertNotIn("secret", str(result))
            fetcher.assert_awaited_once()
            self.assertFalse(resolver._cache)
        cancelled = asyncio.Event()
        async def slow(ids, headers):
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()
        fetcher = AsyncMock(side_effect=slow)
        result = await DiagnosticUserProfileResolver(fetcher, timeout_seconds=0.01).profiles_for(request(), "caller", ["owner"])
        self.assertEqual(result["owner"]["resolution"], "unavailable")
        self.assertTrue(cancelled.is_set())
        fetcher.assert_awaited_once()

    async def test_overall_timeout_covers_all_batches_and_retains_completed_batch(self):
        async def fetch(ids, headers):
            if "owner-100" in ids:
                await asyncio.sleep(10)
            return {user_id: profile(user_id) for user_id in ids}
        fetcher = AsyncMock(side_effect=fetch)
        resolver = DiagnosticUserProfileResolver(fetcher, timeout_seconds=0.02)
        result = await resolver.profiles_for(request(), "caller", [f"owner-{index}" for index in range(201)])
        self.assertEqual(result["owner-0"]["resolution"], "resolved")
        self.assertEqual(result["owner-100"]["resolution"], "unavailable")
        self.assertEqual(result["owner-200"]["resolution"], "unavailable")
        self.assertEqual(fetcher.await_count, 2)

    async def test_auth_failure_clears_caller_cache_and_propagates(self):
        fetcher = AsyncMock(return_value={"owner": profile("owner")})
        resolver = DiagnosticUserProfileResolver(fetcher)
        await resolver.profiles_for(request(), "caller", ["owner"])
        await resolver.profiles_for(request(), "other-caller", ["owner"])
        fetcher.side_effect = DiagnosticUserAuthenticationError(403)
        with self.assertRaises(DiagnosticUserAuthenticationError) as caught:
            await resolver.profiles_for(request(token="expired"), "caller", ["uncached"])
        self.assertEqual(caught.exception.status_code, 403)
        self.assertEqual(len(resolver._cache), 1)
        self.assertEqual(next(iter(resolver._cache))[2], "other-caller")

    async def test_cancellation_is_not_swallowed_or_cached(self):
        fetcher = AsyncMock(side_effect=asyncio.CancelledError())
        resolver = DiagnosticUserProfileResolver(fetcher)
        with self.assertRaises(asyncio.CancelledError):
            await resolver.profiles_for(request(), "caller", ["owner"])
        self.assertFalse(resolver._cache)


class AvatarSafetyTests(unittest.TestCase):
    def test_safe_url_rules(self):
        for value in ("https://assets.example/avatar.png", "/avatar/image.png?v=2", "http://assets.example/image.png"):
            self.assertEqual(safe_avatar_url(value), value)
        self.assertEqual(safe_avatar_url("https://assets.example/image.png#unused"), "https://assets.example/image.png")
        for value in (None, {}, "javascript:alert(1)", "data:image/png;base64,AA", "file:///tmp/image",
                      "//assets.example/image.png", "https://name:secret@assets.example/a.png",
                      "https://assets.example/a.png?access_token=secret", "/avatar.png?Authorization=secret",
                      "/avatar.png?x-user-token=secret", "/avatar.png?id_token=secret",
                      "/avatar.png?x-amz-signature=secret", "https://assets.example:invalid/a.png",
                      "https://assets.example/a.png%0a", "/\\attacker.example/image.png", "a.png", "x" * 2049):
            with self.subTest(value=value):
                self.assertIsNone(safe_avatar_url(value))


if __name__ == "__main__":
    unittest.main()
