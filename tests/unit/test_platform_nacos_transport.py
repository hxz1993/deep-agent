import asyncio
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from aiohttp import ClientConnectionError

from deepfos_platform.errors.classes import APIStatusCodeError
from deepfos_platform.lib.nacos import NacosAPI
from deepfos_platform.options import PlatformOptions


def response(status=200, text='{"doms": []}'):
    return Mock(status=status, text=AsyncMock(return_value=text), release=Mock())


class NacosTransportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        environ = patch.dict(os.environ, {}, clear=True)
        environ.start()
        self.addCleanup(environ.stop)
        option = PlatformOptions()
        self.options = option.nacos
        option_patch = patch('deepfos_platform.options.OPTION', option)
        option_patch.start()
        self.addCleanup(option_patch.stop)
        self.options.server = 'http://registry:8848'

    async def test_default_constructor_uses_platform_auth_options(self):
        option = PlatformOptions()
        with patch('deepfos_platform.options.OPTION', option):
            option.nacos.server = 'http://default-registry:8848'
            option.nacos.access_key = 'default-ak'
            option.nacos.secret_key = 'default-sk'
            option.nacos.auth_mode = 'aksk'
            api = NacosAPI()
        try:
            with patch('deepfos_platform.lib.httpcli.AioHttpCli.get', new_callable=AsyncMock) as request:
                request.return_value = response()
                await api.list_service()
                self.assertEqual(request.call_args.args[0],
                                 'http://default-registry:8848/nacos/v1/ns/service/list')
                self.assertEqual(request.call_args.kwargs['params']['ak'], 'default-ak')
                self.assertNotIn('accessToken', request.call_args.kwargs['params'])
        finally:
            await api.close()

    async def test_aksk_signs_naming_and_config_without_password_login(self):
        self.options.access_key = 'test-ak'
        self.options.secret_key = 'test-sk'
        self.options.auth_mode = 'aksk'
        api = NacosAPI(self.options)
        with patch('deepfos_platform.lib.httpcli.AioHttpCli.get', new_callable=AsyncMock) as request:
            request.return_value = response()
            await api.list_instance('app-server', group='test-group')
            fields = request.call_args.kwargs['params']
            self.assertEqual(fields['ak'], 'test-ak')
            self.assertTrue(fields['data'].endswith('@@test-group@@app-server'))
            self.assertIn('signature', fields)
            self.assertNotIn('accessToken', fields)
            await api.get_configs('test-config', group='test-group', namespace='test-tenant')
            fields = request.call_args.kwargs['params']
            self.assertEqual(fields['Spas-AccessKey'], 'test-ak')
            self.assertIn('Spas-Signature', fields)
            self.assertIn('Timestamp', fields)
        await api.close()

    async def test_multi_address_failover_uses_runtime_transport(self):
        self.options.server = 'http://first:8848,http://second:8848/nacos'
        api = NacosAPI(self.options)
        with patch('deepfos_platform.lib.httpcli.AioHttpCli.get', new_callable=AsyncMock) as request:
            request.side_effect = [ClientConnectionError('connection refused'), response()]
            self.assertEqual(await api.list_service(), {'doms': []})
            self.assertEqual(request.await_count, 2)
            self.assertEqual(request.call_args_list[0].args[0], 'http://first:8848/nacos/v1/ns/service/list')
            self.assertEqual(request.call_args_list[1].args[0], 'http://second:8848/nacos/v1/ns/service/list')
        await api.close()

    async def test_password_403_refresh_retries_once_with_new_token(self):
        self.options.user = 'test-user'
        self.options.password = 'test-password'
        api = NacosAPI(self.options)
        with patch('deepfos_platform.lib.httpcli.AioHttpCli.get', new_callable=AsyncMock) as request, patch(
            'deepfos_platform.lib.httpcli.AioHttpCli.post', new_callable=AsyncMock
        ) as login:
            denied = response(403)
            request.side_effect = [denied, response()]
            login.return_value = response(text='{"accessToken":"new-token", "tokenTtl":3600}')
            self.assertEqual(await api.list_service(), {'doms': []})
            self.assertEqual(request.await_count, 2)
            self.assertEqual(request.call_args.kwargs['params']['accessToken'], 'new-token')
            login.assert_awaited_once()
            denied.release.assert_called_once()
        await api.close()

    async def test_second_403_stops_without_another_refresh(self):
        self.options.user = 'test-user'
        self.options.password = 'test-password'
        api = NacosAPI(self.options)
        denied = [response(403), response(403)]
        try:
            with patch('deepfos_platform.lib.httpcli.AioHttpCli.get', new_callable=AsyncMock) as request, patch(
                'deepfos_platform.lib.httpcli.AioHttpCli.post', new_callable=AsyncMock
            ) as login:
                request.side_effect = denied
                login.return_value = response(text='{"accessToken":"new-token", "tokenTtl":3600}')
                with self.assertRaises(APIStatusCodeError):
                    await api.list_service()
                self.assertEqual(request.await_count, 2)
                login.assert_awaited_once()
                for item in denied:
                    item.release.assert_called_once()
        finally:
            await api.close()

    async def test_static_403_is_not_retried(self):
        self.options.access_key = 'test-ak'
        self.options.secret_key = 'test-sk'
        self.options.auth_mode = 'aksk'
        api = NacosAPI(self.options)
        with patch('deepfos_platform.lib.httpcli.AioHttpCli.get', new_callable=AsyncMock) as request:
            request.return_value = response(403)
            with self.assertRaises(APIStatusCodeError):
                await api.list_service()
            request.assert_awaited_once()
            request.return_value.release.assert_called_once()
        await api.close()

    async def test_cancellation_during_response_read_releases_connection(self):
        api = NacosAPI(self.options)
        received = response()
        reading = asyncio.Event()
        blocked = asyncio.Event()

        async def read_body():
            reading.set()
            await blocked.wait()

        received.text.side_effect = read_body
        task = None
        try:
            with patch('deepfos_platform.lib.httpcli.AioHttpCli.get', new_callable=AsyncMock) as request:
                request.return_value = received
                task = asyncio.create_task(api.list_service())
                await asyncio.wait_for(reading.wait(), timeout=1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                received.release.assert_called_once()
                request.assert_awaited_once()
        finally:
            if task is not None and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await api.close()

    async def test_logs_redact_password_tokens_and_signatures(self):
        self.options.user = 'test-user'
        self.options.password = 'secret-password'
        api = NacosAPI(self.options)
        api.log = Mock()
        with patch('deepfos_platform.lib.httpcli.AioHttpCli.post', new_callable=AsyncMock) as request:
            request.return_value = response(text='{"accessToken":"secret-token", "tokenTtl":3600}')
            await api.maybe_login(schedule_renewal=False)
        with patch('deepfos_platform.lib.httpcli.AioHttpCli.get', new_callable=AsyncMock) as request:
            request.return_value = response()
            await api.list_service()
        logs = str(api.log.call_args_list)
        self.assertNotIn('secret-password', logs)
        self.assertNotIn('secret-token', logs)
        await api.close()
