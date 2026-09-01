from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from deep_agent.web import proxy_compat


class WebFetchProxyCompatibilityTests(unittest.IsolatedAsyncioTestCase):
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
