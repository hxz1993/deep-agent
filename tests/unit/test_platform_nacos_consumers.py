import os
import unittest
from unittest.mock import AsyncMock, Mock, patch

from deepfos_platform.conf.nacos import NacosConfig
from deepfos_platform.discovery.nacos import Nacos
from deepfos_platform.options import PlatformOptions
from deepfos_platform.registry import nacoscli, register


class PlatformRegistrationTests(unittest.TestCase):
    def test_dns_registration_does_not_require_nacos_or_start_heartbeat(self):
        with patch.dict(os.environ, {}, clear=True):
            option = PlatformOptions()
            option.srvdis.implementation = 'dns'
            option.gunicorn.inst_host = '127.0.0.1'
            option.gunicorn.inst_port = '8080'
            with patch('deepfos_platform.options.OPTION', option), patch.object(
                register, 'OPTION', option
            ), patch.object(register, 'run_background') as background:
                register.register_service('app-server')
                background.assert_not_called()


class NacosConsumerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.option = PlatformOptions()
        self.option.nacos.server = 'http://registry:8848'
        self.option.nacos.ram_role_name = 'test-role'
        option_patch = patch('deepfos_platform.options.OPTION', self.option)
        option_patch.start()
        self.addCleanup(option_patch.stop)
        self.option.nacos.auth_mode = 'ram'
        self.api = AsyncMock()

    async def test_discovery_and_registration_use_default_api_and_close(self):
        with patch('deepfos_platform.discovery.nacos.OPTION', self.option), patch(
            'deepfos_platform.discovery.nacos.NacosAPI', return_value=self.api
        ) as factory:
            discovery = Nacos()
            factory.assert_called_once_with()
            await discovery.on_close()
            self.api.close.assert_awaited_once()
        with patch('deepfos_platform.registry.nacoscli.NacosAPI', return_value=self.api) as factory:
            instance = nacoscli._build_instance('app-server', '127.0.0.1', 8080,
                                                5, 'group', 'ns', 'cluster')
            factory.assert_called_once_with()
            await instance.api.close()

    async def test_config_source_closes_provider_on_success_and_failure(self):
        self.api.get_configs.return_value = 'KEY=value'
        environ = {'EXPORT_NACOS_CONFIG_SERVER_ADDR': 'http://config:8848',
                   'EXPORT_NACOS_AUTH_MODE': 'ram', 'EXPORT_NACOS_RAM_ROLE_NAME': 'test-role',
                   'EXPORT_NACOS_CONFIG_USERNAME': 'config-user',
                   'EXPORT_NACOS_CONFIG_PASSWORD': 'config-password',
                   'EXPORT_NACOS_CONFIG_SHARED_CONFIG': 'shared'}
        with patch.dict(os.environ, environ, clear=True), patch(
            'deepfos_platform.conf.nacos.NacosAPI', return_value=self.api
        ) as factory, patch('deepfos_platform.options.OPTION', self.option):
            result = await NacosConfig.get_environ.__wrapped__(NacosConfig())
            self.assertEqual(result, {'KEY': 'value'})
            options = factory.call_args.args[0]
            self.assertEqual(options.server, 'http://config:8848')
            self.assertEqual(options.user, 'config-user')
            self.assertEqual(options.password, 'config-password')
            self.assertEqual(options.auth_mode, 'ram')
            self.assertEqual(options.ram_role_name, 'test-role')
            self.assertEqual(self.option.nacos.server, 'http://registry:8848')
            self.api.close.assert_awaited_once()
            self.api.reset_mock()
            self.api.get_configs.side_effect = RuntimeError('config unavailable')
            with self.assertRaisesRegex(RuntimeError, 'config unavailable'):
                await NacosConfig.get_environ.__wrapped__(NacosConfig())
            self.api.close.assert_awaited_once()

    async def test_config_source_does_not_inherit_registry_credentials(self):
        self.option.nacos.user = 'registry-user'
        self.option.nacos.password = 'registry-password'
        environ = {'EXPORT_NACOS_CONFIG_SERVER_ADDR': 'http://config:8848'}
        with patch.dict(os.environ, environ, clear=True), patch(
            'deepfos_platform.conf.nacos.NacosAPI', return_value=self.api
        ) as factory, patch('deepfos_platform.options.OPTION', self.option):
            self.assertEqual(await NacosConfig.get_environ.__wrapped__(NacosConfig()), {})
            options = factory.call_args.args[0]
            self.assertIsNot(options, self.option.nacos)
            self.assertEqual(options.server, 'http://config:8848')
            self.assertEqual(options.auth_mode, 'password')
            self.assertEqual(options.user, '')
            self.assertEqual(options.password, '')
            self.assertEqual(self.option.nacos.user, 'registry-user')
            self.assertEqual(self.option.nacos.password, 'registry-password')
            self.api.close.assert_awaited_once()

    async def test_registration_failure_clears_singleton_and_closes_provider(self):
        self.assertIsNone(nacoscli._NacosIns)
        for stage in ('initialize', 'register_once'):
            with self.subTest(stage=stage):
                instance = Mock(running=True, api=self.api,
                                initialize=AsyncMock(), register_once=AsyncMock())
                getattr(instance, stage).side_effect = RuntimeError('registration failed')
                with patch.object(nacoscli, '_build_instance', return_value=instance):
                    try:
                        with self.assertRaisesRegex(RuntimeError, 'registration failed'):
                            await nacoscli.register_once('app-server', '127.0.0.1', 8080)
                        self.assertIsNone(nacoscli._NacosIns)
                        self.api.close.assert_awaited_once()
                    finally:
                        nacoscli._NacosIns = None
                        self.api.reset_mock()

    async def test_stop_closes_provider_when_unregister_fails(self):
        instance = Mock(api=self.api, unregister=AsyncMock(side_effect=RuntimeError('unregister failed')))
        with patch.object(nacoscli, '_NacosIns', instance):
            await nacoscli.stop()
            self.assertIsNone(nacoscli._NacosIns)
            self.api.close.assert_awaited_once()
