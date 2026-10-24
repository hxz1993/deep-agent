from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from deep_agent.web import proxy_compat


class WebFetchProxyCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    def test_system_http_proxy_activates_compatibility_without_environment(self):
        for proxies in (
            {"http": "http://127.0.0.1:7897"},
            {"https": "https://127.0.0.1:7897"},
            {"all": "http://127.0.0.1:7897"},
            {"https": "127.0.0.1:7897"},
        ):
            with self.subTest(proxies=proxies), patch.dict(os.environ, {}, clear=True), \
                    patch.object(proxy_compat, "getproxies", return_value=proxies):
                self.assertTrue(proxy_compat._http_tunnel_proxy_configured())

    def test_no_proxy_or_non_http_proxy_does_not_activate_tunnel_compatibility(self):
        for proxies in (
            {}, {"socks": "socks5://127.0.0.1:7897"},
            {"https": "socks5://127.0.0.1:7897"},
            {"all": "socks5h://127.0.0.1:7897"},
            {"ftp": "http://127.0.0.1:7897"},
            {"https": "http://127.0.0.1:7897", "no": "localhost, *"},
            {"https": "http://[invalid]"},
        ):
            with self.subTest(proxies=proxies), patch.dict(os.environ, {}, clear=True), \
                    patch.object(proxy_compat, "getproxies", return_value=proxies):
                self.assertFalse(proxy_compat._http_tunnel_proxy_configured())

    async def test_installed_hooks_follow_system_proxy_without_changing_other_modes(self):
        original = AsyncMock(return_value=["198.18.0.27"])
        public = AsyncMock(return_value=["140.82.114.4"])
        with patch.object(proxy_compat, "_PATCHED", True), \
                patch.object(proxy_compat, "_ORIGINAL_RESOLVE_HOSTNAME", original), \
                patch.object(proxy_compat, "_public_resolve_hostname", public), \
                patch.dict(os.environ, {}, clear=True):
            for proxies, expected in (
                ({"https": "http://127.0.0.1:7897"}, ["140.82.114.4"]),
                ({}, ["198.18.0.27"]),
                ({"all": "socks5://127.0.0.1:7897"}, ["198.18.0.27"]),
            ):
                with self.subTest(proxies=proxies), patch.object(
                    proxy_compat, "getproxies", return_value=proxies,
                ):
                    self.assertEqual(await proxy_compat._resolve_hostname("example.test"), expected)
        self.assertEqual(public.await_count, 1)
        self.assertEqual(original.await_count, 2)

    async def test_intercepted_public_dns_falls_back_without_allowing_fake_addresses(self):
        for answers in ((["198.18.0.11"], ["2001:2::1a"]), (["146.75.47.52"], ["2001:2::1a"])):
            with self.subTest(answers=answers):
                resolver = SimpleNamespace(resolve=AsyncMock(side_effect=answers), nameservers=[])
                fallback = AsyncMock(return_value=["146.75.47.52"])
                with patch.object(proxy_compat.dns.asyncresolver, "Resolver", return_value=resolver), \
                        patch.object(proxy_compat, "_ORIGINAL_RESOLVE_HOSTNAME", fallback):
                    self.assertEqual(await proxy_compat._public_resolve_hostname("example.test"), ["146.75.47.52"])
                fallback.assert_awaited_once_with("example.test")

    async def test_real_public_dns_answer_does_not_add_a_system_lookup(self):
        resolver = SimpleNamespace(resolve=AsyncMock(side_effect=[["140.82.114.4"], []]), nameservers=[])
        fallback = AsyncMock()
        with patch.object(proxy_compat.dns.asyncresolver, "Resolver", return_value=resolver), \
                patch.object(proxy_compat, "_ORIGINAL_RESOLVE_HOSTNAME", fallback):
            self.assertEqual(await proxy_compat._public_resolve_hostname("example.test"), ["140.82.114.4"])
        fallback.assert_not_awaited()

    async def test_nonfake_private_dns_answer_is_left_for_existing_ssrf_rejection(self):
        resolver = SimpleNamespace(resolve=AsyncMock(side_effect=[["10.1.2.3"], []]), nameservers=[])
        fallback = AsyncMock()
        with patch.object(proxy_compat.dns.asyncresolver, "Resolver", return_value=resolver), \
                patch.object(proxy_compat, "_ORIGINAL_RESOLVE_HOSTNAME", fallback):
            self.assertEqual(await proxy_compat._public_resolve_hostname("example.test"), ["10.1.2.3"])
        fallback.assert_not_awaited()

    async def test_proxy_context_uses_public_resolution_only_inside_scope(self) -> None:
        original = AsyncMock(return_value=["197.18.0.27"])
        public = AsyncMock(return_value=["140.82.114.4"])
        with (
            patch.object(proxy_compat, "_ORIGINAL_RESOLVE_HOSTNAME", original),
            patch.object(proxy_compat, "_public_resolve_hostname", public),
            patch.object(proxy_compat, "_PATCHED", False),
            patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:7897"}),
        ):
            self.assertEqual(
                await proxy_compat._resolve_hostname("github.com"),
                ["197.18.0.27"],
            )
            async with proxy_compat.proxy_compatible_web_fetch():
                self.assertEqual(
                    await proxy_compat._resolve_hostname("github.com"),
                    ["140.82.114.4"],
                )

        original.assert_awaited_once_with("github.com")
        public.assert_awaited_once_with("github.com")

    async def test_installed_proxy_compatibility_covers_deferred_media_downloads(
        self,
    ) -> None:
        original = AsyncMock(return_value=["197.18.0.27"])
        public = AsyncMock(return_value=["140.82.114.4"])
        with (
            patch.object(proxy_compat, "_ORIGINAL_RESOLVE_HOSTNAME", original),
            patch.object(proxy_compat, "_public_resolve_hostname", public),
            patch.object(proxy_compat, "_PATCHED", True),
            patch.dict(os.environ, {"HTTPS_PROXY": "http://127.0.0.1:7897"}),
        ):
            self.assertEqual(
                await proxy_compat._resolve_hostname("python.org"),
                ["140.82.114.4"],
            )

        original.assert_not_awaited()
        public.assert_awaited_once_with("python.org")

    async def test_proxy_tunnel_preserves_validated_hostname_for_tls(self) -> None:
        start_tls = AsyncMock(return_value=SimpleNamespace())
        token = proxy_compat._PROXY_TLS_SERVER_NAME.set("github.com")
        try:
            with patch.object(proxy_compat, "_ORIGINAL_START_TLS", start_tls):
                result = await proxy_compat._start_tls(
                    SimpleNamespace(),
                    SimpleNamespace(),
                    "140.82.114.4",
                    30.0,
                )
        finally:
            proxy_compat._PROXY_TLS_SERVER_NAME.reset(token)

        self.assertIsNotNone(result)
        self.assertEqual(start_tls.await_args.args[2], "github.com")
        self.assertEqual(start_tls.await_args.args[3], 30.0)
