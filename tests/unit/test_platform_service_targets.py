import unittest
from unittest.mock import AsyncMock, Mock, patch

from deep_agent.security.console_admin import PlatformEnterpriseAdminChecker
from deep_agent.security.current_user import PlatformCurrentUserFetcher
from deep_agent.security.diagnostic_users import (
    DiagnosticUserUnavailableError, PlatformDiagnosticUserFetcher,
)
from deepfos_platform.client.base import BaseAPI
from deepfos_platform.discovery.target import normalize_service_target


class ServiceTargetTests(unittest.IsolatedAsyncioTestCase):
    def test_base_api_and_adapters_share_the_existing_service_convention(self):
        for target, expected in (
            ("system-server", "system-server"),
            ("http://system-server", "system-server"),
            ("https://space-server", "space-server"),
            ("HTTP://SYSTEM-SERVER", "SYSTEM-SERVER"),
            (" http://system-server ", "system-server"),
            ("seepln-account", "seepln-account"),
        ):
            with self.subTest(target=target):
                self.assertEqual(normalize_service_target(target), expected)
                self.assertEqual(BaseAPI(header={}, server_name=target).server_name, expected)

    async def test_explicit_addresses_and_gateway_paths_still_use_direct_http(self):
        for target in (
            "http://127.0.0.1:8080", "https://system-server.example",
            "http://system-server:8080", "https://test.example/deepfos-server/system-server",
            "http://system-server/api", "http://system-server/",
        ):
            with self.subTest(target=target), patch(
                "deep_agent.security.diagnostic_users.ServiceDiscovery.instantiate"
            ) as discovery:
                self.assertEqual(normalize_service_target(target), target)
                url, success, failure = await PlatformDiagnosticUserFetcher(system_service=target)._resolve_url()
                self.assertEqual(url, target.rstrip("/") + "/user/get-user-detail-list")
                self.assertIsNone(success)
                self.assertIsNone(failure)
                discovery.assert_not_called()

    async def test_account_adapters_resolve_service_urls_and_keep_callbacks(self):
        for fetcher_type, endpoint in (
            (PlatformCurrentUserFetcher, "/api/user/full-detail"),
            (PlatformEnterpriseAdminChecker, "/oauth/checkEnterpriseAdmin"),
        ):
            with self.subTest(fetcher=fetcher_type.__name__):
                success, failure = Mock(), Mock()
                discovery = Mock(closed=False)
                discovery.get_url = AsyncMock(return_value=(success, failure, "http://127.0.0.1:8080"))
                with patch("deepfos_platform.discovery.ServiceDiscovery.instantiate", return_value=discovery):
                    result = await fetcher_type(account_service="http://account-server")._resolve_url()
                discovery.get_url.assert_awaited_once_with("account-server")
                self.assertEqual(result, ("http://127.0.0.1:8080" + endpoint, success, failure))

    async def test_discovery_failure_never_falls_back_to_service_hostname_dns(self):
        discovery = Mock(closed=False)
        discovery.get_url = AsyncMock(side_effect=LookupError("not registered"))
        with patch("deepfos_platform.discovery.ServiceDiscovery.instantiate", return_value=discovery), patch(
            "deep_agent.security.diagnostic_users.AioHttpCli.post", AsyncMock()
        ) as post:
            with self.assertRaises(DiagnosticUserUnavailableError):
                await PlatformDiagnosticUserFetcher(system_service="http://system-server")(("4",), {})
        discovery.get_url.assert_awaited_once_with("system-server")
        post.assert_not_called()
