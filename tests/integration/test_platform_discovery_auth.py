"""Discovery and authentication contracts migrated from the pyserver branches."""
import base64
import hashlib
import hmac
import json
import os
import unittest
from unittest.mock import patch

from aiohttp import web
from aiohttp.test_utils import TestServer

from deepfos_platform.client.base import BaseAPI, post
from deepfos_platform.discovery.discovery import ServiceDiscovery
from deepfos_platform.errors.classes import APIStatusCodeError
from deepfos_platform.lib.httpcli import AioHttpCli
from deepfos_platform.lib.nacos import NacosAPI
from deepfos_platform.options import PlatformOptions


class EchoAPI(BaseAPI):
    @post('/echo', data_wrapped=False, return_raw=True)
    def echo(self, message, trace):
        return {'body': {'message': message}, 'param': {'trace': trace}}


class DNSHTTPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_dns_post_query_body_and_live_address_change(self):
        async def echo(request):
            return web.json_response({'message': (await request.json())['message'],
                                      'trace': request.query.get('trace')})

        app = web.Application()
        app.router.add_post('/echo', echo)
        app.router.add_post('/changed/echo', echo)
        server = TestServer(app)
        await server.start_server()
        self.addAsyncCleanup(server.close)
        self.addAsyncCleanup(AioHttpCli.close_current)
        self.addAsyncCleanup(ServiceDiscovery.stop)
        option = PlatformOptions()
        option.srvdis.implementation = 'dns'
        option.general.system_env = 'must-not-prefix-'
        with patch('deepfos_platform.options.OPTION', option), patch(
            'deepfos_platform.client.base.OPTION', option
        ), patch('deepfos_platform.discovery.discovery.OPTION', option), patch.dict(
            os.environ, {'EXPORT_DNS_APP': str(server.make_url('/'))}, clear=False
        ):
            api = EchoAPI(header={'Content-Type': 'application/json;charset=utf-8'}, server_name='http://app-server')
            result = await api.echo('dns', trace='request-1')
            self.assertEqual(result, {'message': 'dns', 'trace': 'request-1'})
            os.environ['EXPORT_DNS_APP'] = str(server.make_url('/changed'))
            self.assertEqual((await api.get_base_url())[2], str(server.make_url('/changed/')))
            self.assertEqual(await api.echo('changed', trace='request-2'),
                             {'message': 'changed', 'trace': 'request-2'})
            await ServiceDiscovery.stop()


class NacosHTTPIntegrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        environ = patch.dict(os.environ, {}, clear=True)
        environ.start()
        self.addCleanup(environ.stop)
        self.calls = []
        self.expected_token = None
        self.expected_security_token = None
        self.secret_key = 'SK_test'
        self.metadata_content_type = 'text/plain'
        self.metadata_payload = {
            'Code': 'Success', 'AccessKeyId': 'AK_test', 'AccessKeySecret': self.secret_key,
            'SecurityToken': 'test-sts-token', 'Expiration': '2099-01-01T00:00:00Z',
        }
        self.api = None
        self.login_count = 0
        app = web.Application()
        app.router.add_route('*', '/nacos/v1/{tail:.*}', self.handle)
        app.router.add_route('*', '/Gateway/nacos/v1/{tail:.*}', self.handle)
        app.router.add_put('/latest/api/token', self.metadata_token)
        app.router.add_get('/latest/meta-data/ram/security-credentials/test-role', self.metadata_credentials)
        self.server = TestServer(app)
        await self.server.start_server()
        option = PlatformOptions()
        self.options = option.nacos
        option_patch = patch('deepfos_platform.options.OPTION', option)
        option_patch.start()
        self.addCleanup(option_patch.stop)
        self.options.server = str(self.server.make_url('/'))
        self.addAsyncCleanup(self.server.close)
        self.addAsyncCleanup(AioHttpCli.close_current)

    async def asyncTearDown(self):
        if self.api is not None:
            await self.api.close()

    async def metadata_token(self, request):
        self.assertEqual(request.headers['X-aliyun-ecs-metadata-token-ttl-seconds'], '21600')
        return web.Response(text='test-imds-token')

    async def metadata_credentials(self, request):
        self.assertEqual(request.headers['X-aliyun-ecs-metadata-token'], 'test-imds-token')
        return web.json_response(self.metadata_payload, content_type=self.metadata_content_type)

    async def handle(self, request):
        fields = dict(request.query if request.method == 'GET' else await request.post())
        self.calls.append((request.method, request.match_info['tail'], fields))
        endpoint = request.match_info['tail']
        if endpoint == 'auth/login':
            self.login_count += 1
            return web.json_response({'accessToken': f'token-{self.login_count}', 'tokenTtl': 3600})
        if self.expected_token and fields.get('accessToken') != self.expected_token:
            return web.Response(status=403)
        if self.expected_security_token and fields.get('Spas-SecurityToken') != self.expected_security_token:
            return web.Response(status=403)
        if 'ak' in fields:
            signature = base64.b64encode(hmac.new(
                self.secret_key.encode(), fields['data'].encode(), hashlib.sha1
            ).digest()).decode()
            if fields['signature'] != signature:
                return web.Response(status=403)
        if 'Spas-AccessKey' in fields:
            resource = '+'.join(value for value in (fields.get('tenant'), fields.get('group')) if value)
            sign_data = '+'.join(value for value in (resource, fields['Timestamp']) if value)
            signature = base64.b64encode(hmac.new(
                self.secret_key.encode(), sign_data.encode(), hashlib.sha1
            ).digest()).decode()
            if fields['Spas-Signature'] != signature:
                return web.Response(status=403)
        if endpoint == 'ns/service/list':
            return web.json_response({'doms': ['app-server']})
        if endpoint == 'ns/instance/list':
            return web.json_response({'hosts': [
                {'ip': '127.0.0.1', 'port': self.server.port, 'enabled': True, 'healthy': True},
                {'ip': '127.0.0.2', 'port': 1, 'enabled': False, 'healthy': True},
            ]})
        if endpoint == 'cs/configs':
            return web.Response(text='KEY=shared\nQUOTED="value"' if fields['dataId'] == 'shared'
                                else 'KEY=exclusive\nOTHER=present')
        return web.Response(text='ok')

    async def test_aksk_all_naming_routes_config_and_root_suffixes(self):
        suffixes = ('', '/', '/nacos', '/nacos/', '/NACOS', '/NACOS/', '/NaCoS/')
        for suffix in suffixes:
            with self.subTest(suffix=suffix):
                self.options.server = str(self.server.make_url(suffix))
                self.options.access_key = 'AK_test'
                self.options.secret_key = self.secret_key
                self.options.auth_mode = 'aksk'
                self.options.user = 'ignored-user'
                self.options.password = 'ignored-password'
                self.api = NacosAPI(self.options)
                self.assertTrue(await self.api.maybe_login(schedule_renewal=False))
                self.assertEqual(await self.api.list_service(), {'doms': ['app-server']})
                await self.api.list_instance('app-server', group='test-group')
                await self.api.register_instance('app-server', '127.0.0.1', 8080, group='test-group')
                await self.api.send_heartbeat('app-server', '127.0.0.1', 8080, group='test-group')
                await self.api.unregister_instance('app-server', '127.0.0.1', 8080, group='test-group')
                await self.api.get_configs('shared', group='test-group', namespace='test-tenant')
                await self.api.close()
        self.assertEqual(self.login_count, 0)
        self.assertEqual(len(self.calls), len(suffixes) * 6)
        for method, endpoint, fields in self.calls:
            self.assertFalse({'accessToken', 'username', 'password', 'Spas-SecurityToken'} & fields.keys())
            if endpoint.startswith('ns/'):
                self.assertEqual(fields['ak'], 'AK_test')
                if endpoint == 'ns/service/list':
                    self.assertTrue(fields['data'].isdigit())
                else:
                    self.assertTrue(fields['data'].endswith('@@test-group@@app-server'))
            else:
                self.assertEqual(fields['Spas-AccessKey'], 'AK_test')
            if endpoint == 'ns/instance' and method in ('POST', 'DELETE'):
                self.assertEqual(fields['ip'], '127.0.0.1')
                self.assertEqual(fields['port'], '8080')
            if endpoint == 'ns/instance/beat':
                self.assertEqual(json.loads(fields['beat'])['port'], 8080)

    async def test_nacos_context_normalization_preserves_gateway_path_case(self):
        self.options.server = str(self.server.make_url('/Gateway/NACOS/'))
        self.options.access_key = 'AK_test'
        self.options.secret_key = self.secret_key
        self.options.auth_mode = 'aksk'
        self.api = NacosAPI(self.options)
        self.assertEqual(await self.api.list_service(), {'doms': ['app-server']})
        self.assertEqual(self.calls[-1][2]['ak'], 'AK_test')

    async def test_password_403_refreshes_and_anonymous_does_not_login(self):
        self.options.user = 'user'
        self.options.password = 'password'
        self.api = NacosAPI(self.options)
        await self.api.maybe_login(schedule_renewal=False)
        self.expected_token = 'token-2'
        await self.api.list_service()
        self.assertEqual([endpoint for _, endpoint, _ in self.calls],
                         ['auth/login', 'ns/service/list', 'auth/login', 'ns/service/list'])
        self.assertEqual(self.calls[-1][2]['accessToken'], 'token-2')
        await self.api.close()
        self.expected_token = None
        self.calls.clear()
        self.options.user = ''
        self.options.password = ''
        self.options.access_key = 'not-opted-in'
        self.options.secret_key = 'unused'
        self.api = NacosAPI(self.options)
        self.assertFalse(await self.api.maybe_login())
        await self.api.list_service()
        self.assertEqual(len(self.calls), 1)
        self.assertNotIn('ak', self.calls[0][2])

    async def test_ram_403_refresh_rebuilds_naming_and_config_credentials(self):
        from unittest.mock import AsyncMock
        from deepfos_platform.lib.nacos_auth import StsCredentials
        self.expected_security_token = 'new-token'
        self.secret_key = 'new-secret'
        self.options.ram_role_name = 'role'
        self.options.auth_mode = 'ram'
        self.api = NacosAPI(self.options)
        fetch = AsyncMock(side_effect=[
            StsCredentials('old-ak', 'old-secret', 'old-token', 4102444800),
            StsCredentials('new-ak', 'new-secret', 'new-token', 4102444800),
        ])
        with patch.object(self.api.auth_provider, '_fetch_credentials', fetch):
            await self.api.maybe_login(schedule_renewal=False)
            self.assertEqual(await self.api.list_service(), {'doms': ['app-server']})
            self.assertEqual(fetch.await_count, 2)
            self.assertEqual(len(self.calls), 2)
            old, new = self.calls[0][2], self.calls[1][2]
            self.assertEqual((old['ak'], old['Spas-SecurityToken']), ('old-ak', 'old-token'))
            self.assertEqual((new['ak'], new['Spas-SecurityToken']), ('new-ak', 'new-token'))
            self.assertNotEqual(old['signature'], new['signature'])
            await self.api.get_configs('shared', group='test-group', namespace='test-tenant')
            fields = self.calls[-1][2]
            self.assertEqual(fields['Spas-AccessKey'], 'new-ak')
            self.assertEqual(fields['Spas-SecurityToken'], 'new-token')
            self.assertEqual(fetch.await_count, 2)
        self.assertEqual(self.login_count, 0)

    async def test_ram_initialization_accepts_json_metadata_with_non_json_content_type(self):
        get, put = AioHttpCli.get, AioHttpCli.put

        async def metadata_get(url, **kwargs):
            url = url.replace('http://100.100.100.200', str(self.server.make_url('')).rstrip('/'))
            return await get(url, **kwargs)

        async def metadata_put(url, **kwargs):
            url = url.replace('http://100.100.100.200', str(self.server.make_url('')).rstrip('/'))
            return await put(url, **kwargs)

        self.options.ram_role_name = 'test-role'
        self.options.auth_mode = 'ram'
        self.expected_security_token = 'test-sts-token'
        with patch.object(AioHttpCli, 'get', side_effect=metadata_get), patch.object(
            AioHttpCli, 'put', side_effect=metadata_put
        ):
            for content_type in ('text/plain', 'application/octet-stream', 'application/json'):
                with self.subTest(content_type=content_type):
                    self.metadata_content_type = content_type
                    self.api = NacosAPI(self.options)
                    self.assertTrue(await self.api.maybe_login(schedule_renewal=False))
                    self.assertEqual(await self.api.list_service(), {'doms': ['app-server']})
                    self.assertEqual(self.calls[-1][2]['ak'], 'AK_test')
                    self.assertEqual(self.calls[-1][2]['Spas-SecurityToken'], 'test-sts-token')
                    await self.api.close()
            for update in ({'Code': 'Denied'}, {'SecurityToken': ''},
                           {'Expiration': '2000-01-01T00:00:00Z'}):
                with self.subTest(invalid=update):
                    self.metadata_payload.update(update)
                    self.api = NacosAPI(self.options)
                    calls_before = len(self.calls)
                    with self.assertRaisesRegex(RuntimeError, 'Unable to fetch valid Nacos RAM credentials'):
                        await self.api.maybe_login(schedule_renewal=False)
                    self.assertEqual(len(self.calls), calls_before)
                    self.metadata_payload.update(Code='Success', SecurityToken='test-sts-token',
                                                 Expiration='2099-01-01T00:00:00Z')
                    await self.api.close()
        self.assertEqual(self.login_count, 0)

    async def test_static_403_never_retries_or_logs_in(self):
        self.options.access_key = 'AK_test'
        self.options.secret_key = 'wrong-secret'
        self.options.auth_mode = 'aksk'
        self.api = NacosAPI(self.options)
        with self.assertRaises(APIStatusCodeError):
            await self.api.list_service()
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.login_count, 0)

    async def test_config_source_uses_common_auth_and_exclusive_override(self):
        from deepfos_platform.conf.nacos import NacosConfig
        environ = {'EXPORT_NACOS_CONFIG_SERVER_ADDR': str(self.server.make_url('/')),
                   'EXPORT_NACOS_AUTH_MODE': 'aksk', 'NACOS_ACCESS_KEY': 'AK_test',
                   'NACOS_SECRET_KEY': self.secret_key,
                   'EXPORT_NACOS_CONFIG_NAMESPACE': 'test-tenant',
                   'EXPORT_NACOS_CONFIG_GROUP': 'test-group',
                   'EXPORT_NACOS_CONFIG_SHARED_CONFIG': 'shared',
                   'EXPORT_NACOS_CONFIG_EXT_CONFIG': 'exclusive'}
        with patch.dict(os.environ, environ, clear=True):
            self.assertEqual(await NacosConfig().get_environ(),
                             {'KEY': 'exclusive', 'QUOTED': 'value', 'OTHER': 'present'})
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all('Spas-Signature' in fields for _, _, fields in self.calls))

    async def test_discovery_and_registration_use_auth_options(self):
        from deepfos_platform.discovery.nacos import Nacos
        from deepfos_platform.registry import nacoscli
        option = PlatformOptions()
        option.nacos.server = str(self.server.make_url('/'))
        option.nacos.access_key = 'AK_test'
        option.nacos.secret_key = self.secret_key
        with patch('deepfos_platform.discovery.nacos.OPTION', option), patch(
            'deepfos_platform.options.OPTION', option
        ):
            option.nacos.auth_mode = 'aksk'
            discovery = Nacos()
            try:
                await discovery.on_startup()
                callbacks = await discovery.get_url('app-server')
                self.assertEqual(callbacks[2], f'http://127.0.0.1:{self.server.port}')
                await nacoscli.register_once('app-server', '127.0.0.1', 8080)
                await nacoscli._NacosIns.send_hearbeat()
            finally:
                await discovery.on_close()
                await nacoscli.stop()
        self.assertTrue(all('signature' in fields for _, _, fields in self.calls))
        self.assertEqual(self.login_count, 0)
