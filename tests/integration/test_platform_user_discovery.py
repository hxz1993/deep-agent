"""Exercise real Eureka XML lookup and HTTP transport on loopback fixtures."""
import asyncio
import unittest
from unittest.mock import Mock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from deep_agent.security.diagnostic_users import PlatformDiagnosticUserFetcher
from deepfos_platform.discovery import Eureka, ServiceDiscovery
from deepfos_platform.lib.httpcli import AioHttpCli


class PlatformUserDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_service_name_reaches_registered_instance_and_resolves_legacy_id(self):
        calls = []

        async def users(request):
            calls.append((request.path, await request.json(), request.headers.get("Cookie")))
            return web.json_response({"status": True, "data": [{"userId": 4, "userName": "legacy-user"}]})

        async def registry(request):
            calls.append(request.path)
            return web.Response(text=f"""<applications>
                <versions__delta>1</versions__delta><apps__hashcode>UP_1_</apps__hashcode>
                <application><name>SYSTEM-SERVER</name><instance>
                <ipAddr>127.0.0.1</ipAddr><port enabled="true">{server.port}</port>
                <securePort enabled="false">443</securePort><status>UP</status>
                <actionType>ADDED</actionType></instance></application></applications>""",
                content_type="application/xml")

        app = web.Application()
        app.router.add_get("/eureka/apps/delta", registry)
        app.router.add_post("/user/get-user-detail-list", users)
        server = TestServer(app)
        discovery = None
        try:
            await server.start_server()
            options = Mock()
            options.eureka.server = str(server.make_url("/eureka"))
            options.srvdis.cache_strategy = "ranked"
            with patch("deepfos_platform.discovery.eureka.OPTION", options):
                discovery = Eureka(prefer_https=False)
            discovery._closed = asyncio.Event()
            # Only substitute the registry configuration; discovery, XML parsing,
            # instance selection and the resulting user request are real.
            with patch.object(ServiceDiscovery, "instantiate", return_value=discovery):
                result = await PlatformDiagnosticUserFetcher(system_service="http://system-server")(
                    ("4",), {"cookie": "fixture-token"})
            self.assertEqual(calls, ["/eureka/apps/delta", ("/user/get-user-detail-list", ["4"], "fixture-token")])
            self.assertEqual(result["4"]["display_name"], "legacy-user")
        finally:
            if discovery is not None:
                await discovery.close()
            await AioHttpCli.close_current()
            await server.close()
