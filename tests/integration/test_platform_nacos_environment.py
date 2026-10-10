"""Optional, read-only checks: absence of deployment credentials is a skip."""
import os
import unittest

from aiohttp import ClientSession, ClientTimeout

from deepfos_platform.lib.httpcli import AioHttpCli
from deepfos_platform.lib.nacos import NacosAPI
from deepfos_platform.options import PlatformOptions


class NacosEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncTearDown(self):
        await AioHttpCli.close_current()

    async def test_discovery_endpoint_reachable(self):
        await self.check_reachable('EXPORT_NACOS_SERVER_ADDR')

    async def test_config_endpoint_reachable(self):
        await self.check_reachable('EXPORT_NACOS_CONFIG_SERVER_ADDR')

    async def check_reachable(self, key):
        server = os.getenv(key)
        if not server:
            self.skipTest(f'{key} not configured')
        bases = NacosAPI._resolve_base_urls(server).sort()
        async with ClientSession(timeout=ClientTimeout(total=5)) as client:
            for base in bases:
                async with client.get(base + '/ns/service/list',
                                      params={'pageNo': 1, 'pageSize': 1}) as response:
                    await response.read()
                    self.assertLess(response.status, 500)

    async def test_password_login_read_only(self):
        server = os.getenv('EXPORT_NACOS_SERVER_ADDR')
        user, password = os.getenv('EXPORT_NACOS_USER_NAME'), os.getenv('EXPORT_NACOS_PASSWORD')
        if not (server and user and password):
            self.skipTest('Discovery server and password credentials not configured')
        options = PlatformOptions().nacos
        options.server = server
        options.auth_mode = 'password'
        options.user = user
        options.password = password
        api = NacosAPI(options)
        try:
            self.assertTrue(await api.maybe_login(schedule_renewal=False))
            self.assertTrue(api._token)
        finally:
            await api.close()

    async def test_aksk_service_listing_read_only(self):
        await self.check_signed_listing('aksk')

    async def test_ram_service_listing_read_only(self):
        await self.check_signed_listing('ram')

    async def check_signed_listing(self, mode):
        server = os.getenv('EXPORT_NACOS_SERVER_ADDR')
        access_key, secret_key = os.getenv('NACOS_ACCESS_KEY'), os.getenv('NACOS_SECRET_KEY')
        role = os.getenv('EXPORT_NACOS_RAM_ROLE_NAME')
        configured = access_key and secret_key if mode == 'aksk' else role
        if not (server and os.getenv('EXPORT_NACOS_AUTH_MODE') == mode and configured):
            self.skipTest(f'Explicit {mode} mode, server and credentials not configured')
        options = PlatformOptions().nacos
        options.server = server
        options.access_key = access_key or ''
        options.secret_key = secret_key or ''
        options.ram_role_name = role or ''
        options.auth_mode = mode
        api = NacosAPI(options)
        try:
            self.assertTrue(await api.maybe_login(schedule_renewal=False))
            result = await api.list_service(group=os.getenv('EXPORT_NACOS_GROUP', 'DEFAULT_GROUP'),
                                            namespace=os.getenv('EXPORT_NACOS_NAMESPACE', 'public'))
            self.assertIsInstance(result['doms'], list)
        finally:
            await api.close()
