from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import web
from fastapi import Request

from deepfos_platform.lib.httpcli import AioHttpCli

from deep_agent.services.scope_names import (
    PlatformScopeDirectoryFetcher,
    ScopeDirectory,
    ScopeDirectoryUnavailableError,
    ScopeNameResolver,
    normalize_scope_directory,
    resolve_scope_directory,
)


DIRECTORY = {"tenant": [{
    "space": "budget", "spaceName": "预算空间", "spaceUrl": "https://ignored.example/secret",
    "appList": [
        {"app": "budget001", "appName": "年度预算"},
        {"app": "budget002", "appName": "年度预算"},
    ],
}]}


def request(*, token="credential", host="environment.example", language="zh-cn", extra=()):
    return Request({
        "type": "http", "scheme": "https", "path": "/api/v1/sessions", "root_path": "",
        "query_string": b"", "server": (host, 443),
        "headers": [
            (b"host", host.encode()), (b"user", b"untrusted-header-user"),
            (b"language", language.encode()),
            *([(b"authorization", f"Bearer {token}".encode())] if token else []),
            *extra,
        ],
    })


class Response:
    def __init__(self, payload=None, *, status=200, body=None):
        self.status, self.released = status, False
        self._body = body if body is not None else json.dumps(payload).encode()
        self.content = self

    async def iter_chunked(self, size):
        for index in range(0, len(self._body), size):
            yield self._body[index:index + size]

    def release(self):
        self.released = True


class ScopeDirectoryNormalizeTests(unittest.TestCase):
    def test_plain_wrapped_same_name_codes_and_deleted_names(self):
        plain = normalize_scope_directory(DIRECTORY)
        wrapped = normalize_scope_directory({"status": True, "data": DIRECTORY})
        self.assertEqual(plain, wrapped)
        self.assertEqual(plain.apps, {"budget001": "年度预算", "budget002": "年度预算"})
        self.assertEqual(plain.spaces, {"budget": "预算空间"})
        self.assertNotIn("spaceUrl", plain.spaces)
        self.assertIsNone(plain.apps.get("deleted001"))
        self.assertEqual(normalize_scope_directory({}).apps, {})

    def test_chinese_multilingual_names_ignore_missing_names(self):
        directory = normalize_scope_directory({"tenant": [{
            "space": "a", "spaceName": '{"en":"English","zh-CN":"中文空间"}',
            "appList": [
                {"app": "a001", "appName": {"zh_CN": "中文应用", "en": "English app"}},
                {"app": "a002", "appName": ""},
                {"app": "a003", "appName": {"en": "English only"}},
            ],
        }]})
        self.assertEqual(directory.spaces, {"a": "中文空间"})
        self.assertEqual(directory.apps, {"a001": "中文应用"})

    def test_invalid_upstream_payloads_never_become_names(self):
        for payload in ([], None, {"status": False, "message": "secret"},
                        {"code": 403, "data": DIRECTORY}, {"statusCode": 401, "data": DIRECTORY}, {"tenant": "wrong"},
                        {"status": True, "data": []}):
            with self.subTest(payload=payload), self.assertRaises(ScopeDirectoryUnavailableError):
                normalize_scope_directory(payload)


class PlatformScopeDirectoryFetcherTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_http_contract_and_release(self):
        response = Response(DIRECTORY)
        with patch("deep_agent.services.scope_names.AioHttpCli.post", AsyncMock(return_value=response)) as post:
            result = await PlatformScopeDirectoryFetcher(system_service="https://system.example/gateway/system-server")(
                {"user": "trusted-current-user", "authorization": "Bearer test"},
            )
        self.assertEqual(result.apps["budget001"], "年度预算")
        self.assertEqual(post.call_args.args, ("https://system.example/gateway/system-server/space-app-list",))
        args = post.call_args.kwargs
        self.assertEqual(args["headers"]["user"], "trusted-current-user")
        self.assertEqual(args["params"], {"enableSpecialGroup": "false"})
        self.assertEqual(args["json"], [])
        self.assertFalse(args["allow_redirects"])
        self.assertEqual(args["timeout"].total, 2)
        self.assertTrue(response.released)

    async def test_logical_http_service_uses_discovery_and_configured_gateway_does_not(self):
        discovery = Mock(closed=False)
        success, failure = Mock(), Mock()
        discovery.get_url = AsyncMock(return_value=(success, failure, "http://127.0.0.1:39001"))
        response = Response(DIRECTORY)
        with patch("deep_agent.services.scope_names.ServiceDiscovery.instantiate", return_value=discovery), patch(
            "deep_agent.services.scope_names.AioHttpCli.post", AsyncMock(return_value=response),
        ) as post:
            await PlatformScopeDirectoryFetcher(system_service="http://system-server")({"user": "caller"})
        discovery.get_url.assert_awaited_once_with("system-server")
        self.assertEqual(post.call_args.args[0], "http://127.0.0.1:39001/space-app-list")
        success.assert_called_once()
        failure.assert_not_called()
        with patch("deep_agent.services.scope_names.ServiceDiscovery.instantiate") as instantiate:
            url, _, _ = await PlatformScopeDirectoryFetcher(system_service="http://system-server:1234/gateway")._resolve_url()
        self.assertEqual(url, "http://system-server:1234/gateway/space-app-list")
        instantiate.assert_not_called()

    async def test_failures_release_http_response(self):
        cases = [Response({}, status=401), Response({}, status=403), Response({}, status=503),
                 Response(body=b"broken json"), Response({"status": False, "message": "secret"}),
                 Response(body=b"x" * (2 * 1024 * 1024 + 1))]
        for response in cases:
            with self.subTest(status=response.status), patch(
                "deep_agent.services.scope_names.AioHttpCli.post", AsyncMock(return_value=response),
            ):
                with self.assertRaises((ScopeDirectoryUnavailableError, ValueError)):
                    await PlatformScopeDirectoryFetcher(system_service="https://system.example")({"user": "caller"})
            self.assertTrue(response.released)

    async def test_invalid_service_url_never_sends_authentication(self):
        for target in ("https://user:pass@system.example", "https://system.example?key=secret", "ftp://system.example"):
            with self.subTest(target=target), patch("deep_agent.services.scope_names.AioHttpCli.post") as post:
                with self.assertRaises(ScopeDirectoryUnavailableError):
                    await PlatformScopeDirectoryFetcher(system_service=target)({"authorization": "Bearer private"})
                post.assert_not_called()

    async def test_real_http_directory_request_uses_trusted_auth_and_chinese_language(self):
        received = []

        async def directory(http_request):
            body = await http_request.read()
            received.append(({key.lower(): value for key, value in http_request.headers.items()}, dict(http_request.query), body))
            if http_request.content_type != "application/json" or json.loads(body) != []:
                return web.json_response({"status": False, "code": 999999, "message": "invalid JSON contract"})
            return web.json_response(DIRECTORY)

        application = web.Application()
        application.router.add_post("/space-app-list", directory)
        runner = web.AppRunner(application)
        await runner.setup()
        try:
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            resolver = ScopeNameResolver(PlatformScopeDirectoryFetcher(system_service=f"http://127.0.0.1:{port}"))
            result = await resolver.directory_for(request(language="en"), "trusted-user")
            self.assertEqual(result.apps["budget001"], "年度预算")
            self.assertEqual(len(received), 1)
            headers, query, body = received[0]
            self.assertEqual(headers["user"], "trusted-user")
            self.assertEqual(headers["language"], "zh-cn")
            self.assertEqual(headers["authorization"], "Bearer credential")
            self.assertEqual(query, {"enableSpecialGroup": "false"})
            self.assertEqual(headers["content-type"], "application/json")
            self.assertEqual(json.loads(body), [])
        finally:
            await AioHttpCli.close_current()
            await runner.cleanup()


class ScopeNameResolverTests(unittest.IsolatedAsyncioTestCase):
    async def test_no_login_or_invalid_credentials_skip_fetch(self):
        fetcher = AsyncMock(return_value=ScopeDirectory())
        resolver = ScopeNameResolver(fetcher)
        self.assertEqual(await resolver.directory_for(request(token=None), "trusted-user"), ScopeDirectory())
        self.assertEqual(await resolver.directory_for(request(extra=[(b"authorization", b"second")]), "trusted-user"), ScopeDirectory())
        self.assertEqual(await resolver.directory_for(request(), ""), ScopeDirectory())
        fetcher.assert_not_awaited()

    async def test_trusted_identity_chinese_language_and_no_user_chosen_target(self):
        fetcher = AsyncMock(return_value=ScopeDirectory(apps={"budget001": "年度预算"}))
        resolver = ScopeNameResolver(fetcher)
        await resolver.directory_for(request(language="en", extra=[(b"app", b"some001"), (b"space", b"some")]), "trusted-user")
        headers = fetcher.call_args.args[0]
        self.assertEqual(headers["user"], "trusted-user")
        self.assertEqual(headers["language"], "zh-cn")
        self.assertNotIn("app", headers)
        self.assertNotIn("space", headers)

    async def test_cache_isolates_user_auth_origin_language_and_deployment(self):
        fetcher = AsyncMock(return_value=ScopeDirectory(apps={"budget001": "名称"}))
        resolver = ScopeNameResolver(fetcher, deployment_scope="test")
        for req, user in ((request(), "u1"), (request(), "u1"), (request(), "u2"),
                          (request(token="different"), "u1"), (request(host="other.example"), "u1"),
                          (request(language="en"), "u1")):
            await resolver.directory_for(req, user)
        self.assertEqual(fetcher.await_count, 5)
        other = ScopeNameResolver(fetcher, deployment_scope="alpha")
        await other.directory_for(request(), "u1")
        self.assertEqual(fetcher.await_count, 6)
        self.assertNotIn("credential", repr(tuple(resolver._cache)))
        self.assertEqual(len(resolver._cache), 5)

    async def test_cache_lru_ttl_and_response_copy(self):
        now = [10.0]
        fetcher = AsyncMock(return_value=ScopeDirectory(apps={"budget001": "预算"}))
        resolver = ScopeNameResolver(fetcher, cache_max_entries=2, clock=lambda: now[0])
        first = await resolver.directory_for(request(), "u1")
        first.apps.clear()
        self.assertEqual((await resolver.directory_for(request(), "u1")).apps, {"budget001": "预算"})
        await resolver.directory_for(request(), "u2")
        await resolver.directory_for(request(), "u3")
        self.assertEqual(len(resolver._cache), 2)
        self.assertFalse(any(key[2] == "u1" for key in resolver._cache))
        now[0] = 311
        await resolver.directory_for(request(), "u2")
        self.assertEqual(fetcher.await_count, 4)
        self.assertEqual(len(resolver._cache), 1)

    async def test_cache_never_keeps_more_than_256_caller_catalogs(self):
        fetcher = AsyncMock(return_value=ScopeDirectory())
        resolver = ScopeNameResolver(fetcher, cache_max_entries=1000)
        for index in range(260):
            await resolver.directory_for(request(), f"u{index}")
        self.assertEqual(len(resolver._cache), 256)
        await resolver.directory_for(request(), "u0")
        self.assertEqual(fetcher.await_count, 261)
        self.assertEqual(len(resolver._cache), 256)

    async def test_unavailable_directory_does_not_block_every_filter_request_and_recovers(self):
        now = [10.0]
        fetcher = AsyncMock(side_effect=TimeoutError())
        resolver = ScopeNameResolver(fetcher, clock=lambda: now[0])
        for _ in range(4):
            self.assertEqual(await resolver.directory_for(request(), "u1"), ScopeDirectory())
        self.assertEqual(fetcher.await_count, 1)
        self.assertEqual(resolver._cache, {})
        self.assertEqual(len(resolver._failures), 1)
        # A different caller or credential must not inherit a failed lookup.
        await resolver.directory_for(request(), "u2")
        await resolver.directory_for(request(token="changed"), "u1")
        self.assertEqual(fetcher.await_count, 3)
        fetcher.side_effect = None
        fetcher.return_value = ScopeDirectory(apps={"budget001": "恢复后的名称"})
        now[0] = 20.0
        self.assertEqual((await resolver.directory_for(request(), "u1")).apps["budget001"], "恢复后的名称")
        self.assertEqual(fetcher.await_count, 4)
        self.assertNotIn("credential", repr(tuple(resolver._failures)))

    async def test_failure_backoff_is_bounded(self):
        resolver = ScopeNameResolver(AsyncMock(side_effect=TimeoutError()), cache_max_entries=2)
        for user in ("u1", "u2", "u3"):
            await resolver.directory_for(request(), user)
        self.assertEqual(len(resolver._failures), 2)

    async def test_coalesces_concurrent_lookup_and_reader_cancel_does_not_cancel_others(self):
        started, finish = asyncio.Event(), asyncio.Event()

        async def fetch(headers):
            started.set()
            await finish.wait()
            return ScopeDirectory(apps={"a001": "应用"})

        fetcher = AsyncMock(side_effect=fetch)
        resolver = ScopeNameResolver(fetcher)
        one = asyncio.create_task(resolver.directory_for(request(), "u"))
        two = asyncio.create_task(resolver.directory_for(request(), "u"))
        await started.wait()
        one.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await one
        finish.set()
        self.assertEqual((await two).apps, {"a001": "应用"})
        self.assertEqual(fetcher.await_count, 1)
        await asyncio.sleep(0)
        self.assertEqual(resolver._inflight, {})

    async def test_whole_discovery_deadline_falls_back_without_caching_failure(self):
        fetcher = PlatformScopeDirectoryFetcher(system_service="system-server")
        cancelled = asyncio.Event()

        async def slow_discovery():
            try:
                await asyncio.sleep(10)
            finally:
                cancelled.set()

        resolver = ScopeNameResolver(fetcher, timeout_seconds=0.02, failure_backoff_seconds=0)
        with patch.object(fetcher, "_resolve_url", slow_discovery):
            self.assertEqual(await resolver.directory_for(request(), "u"), ScopeDirectory())
        self.assertTrue(cancelled.is_set())
        self.assertEqual(resolver._cache, {})
        resolver._fetcher = AsyncMock(return_value=ScopeDirectory(apps={"a001": "恢复"}))
        self.assertEqual((await resolver.directory_for(request(), "u")).apps, {"a001": "恢复"})

    async def test_upstream_auth_rejection_and_shape_error_do_not_break_session_facts(self):
        for error in (PermissionError("sensitive"), ScopeDirectoryUnavailableError("private body"), ValueError("bad")):
            resolver = ScopeNameResolver(AsyncMock(side_effect=error))
            self.assertEqual(await resolver.directory_for(request(), "u"), ScopeDirectory())
        resolver = ScopeNameResolver(AsyncMock(return_value={"apps": {"a001": "wrong"}}))
        self.assertEqual(await resolver.directory_for(request(), "u"), ScopeDirectory())

    async def test_public_entry_handles_unconfigured_platform_without_network(self):
        options = Mock()
        options.server.system = "http://system-server"
        options.srvdis.implementation = "eureka"
        options.k8s.namespace = "namespace"
        options.eureka.server = "http://discovery.example/eureka"
        with patch("deep_agent.services.scope_names.PLATFORM_OPTION", options), patch(
            "deep_agent.services.scope_names.AioHttpCli.post",
        ) as post:
            self.assertEqual(await resolve_scope_directory(request(token=None), "u"), ScopeDirectory())
        post.assert_not_called()

    async def test_configured_direct_system_does_not_require_eureka_configuration(self):
        options = Mock()
        options.server.system = "https://direct-system.example/gateway"
        options.srvdis.implementation = "eureka"
        options.k8s.namespace = None
        # A production direct target may have no discovery server configured.
        del options.eureka
        with patch("deep_agent.services.scope_names.PLATFORM_OPTION", options), patch(
            "deep_agent.services.scope_names.AioHttpCli.post", AsyncMock(return_value=Response(DIRECTORY)),
        ) as post:
            directory = await resolve_scope_directory(request(), "direct-user")
        self.assertEqual(directory.apps["budget001"], "年度预算")
        post.assert_awaited_once()

    async def test_local_gateway_and_platform_environment_change_cannot_reuse_names(self):
        options = Mock()
        options.server.system = "system-server"
        options.server.base = "https://alpha.example/gateway"
        options.srvdis.implementation = "local"
        options.k8s.namespace = None
        options.general.system_env = "alpha"
        discovery = Mock(closed=False)
        discovery.get_url = AsyncMock(side_effect=lambda service: (None, None, options.server.base + "/" + service))
        post = AsyncMock(side_effect=[Response(DIRECTORY), Response({"t": [{
            "space": "budget", "spaceName": "Cloud空间",
            "appList": [{"app": "budget001", "appName": "Cloud预算"}],
        }]}), Response({"t": [{"space": "budget", "appList": [{"app": "budget001", "appName": "新环境"}]}]})])
        with patch("deep_agent.services.scope_names.PLATFORM_OPTION", options), patch(
            "deep_agent.services.scope_names.ServiceDiscovery.instantiate", return_value=discovery,
        ), patch("deep_agent.services.scope_names.AioHttpCli.post", post):
            self.assertEqual((await resolve_scope_directory(request(), "gateway-isolation-user")).apps["budget001"], "年度预算")
            options.server.base = "https://cloud.example/gateway"
            self.assertEqual((await resolve_scope_directory(request(), "gateway-isolation-user")).apps["budget001"], "Cloud预算")
            options.general.system_env = "another-environment"
            self.assertEqual((await resolve_scope_directory(request(), "gateway-isolation-user")).apps["budget001"], "新环境")
        self.assertEqual(post.await_count, 3)


if __name__ == "__main__":
    unittest.main()
