import os
import unittest
from unittest.mock import patch

from deepfos_platform.client.base import BaseAPI
from deepfos_platform.discovery.discovery import ServiceDiscovery
from deepfos_platform.discovery.target import normalize_service_target
from deepfos_platform.options import PlatformOptions


class DNSDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.option = PlatformOptions()
        self.option.srvdis.implementation = 'dns'
        self.option.general.system_env = 'dev-'
        for module in ('deepfos_platform.options', 'deepfos_platform.client.base',
                       'deepfos_platform.discovery.discovery'):
            patcher = patch(module + '.OPTION', self.option)
            patcher.start()
            self.addCleanup(patcher.stop)

    async def asyncTearDown(self):
        await ServiceDiscovery.stop()

    async def test_dns_selection_is_dynamic_and_has_no_refresh_task(self):
        with patch.dict(os.environ, {'EXPORT_DNS_APP': 'http://app.example:8080'}, clear=True):
            from deepfos_platform.discovery.dns import DNS
            with patch('deepfos_platform.discovery.discovery.asyncio.create_task') as task:
                await ServiceDiscovery.start()
                discovery = ServiceDiscovery.instantiate()
                self.assertIsInstance(discovery, DNS)
                success, failure, url = await discovery.get_url('app-server')
                self.assertEqual(url, 'http://app.example:8080')
                self.assertIsNone(success())
                self.assertIsNone(failure())
                os.environ['EXPORT_DNS_APP'] = 'https://changed.example/api'
                self.assertEqual((await discovery.get_url('APP-SERVER'))[2], 'https://changed.example/api')
                task.assert_not_called()
            await ServiceDiscovery.stop()
            self.assertIsNone(ServiceDiscovery.__ins__)

    def test_full_source_mapping_and_configuration_errors(self):
        from deepfos_platform.discovery.dns import DNS_ENV_BY_SERVER, DNSConfigurationError, get_dns_url, get_dns_records
        from deepfos_platform.errors.classes import ServerNotFound
        environ = {key: f'https://service-{i}.example' for i, key in enumerate(DNS_ENV_BY_SERVER.values())}
        self.assertEqual(len(DNS_ENV_BY_SERVER), 50)
        self.assertEqual(len(get_dns_records(environ)), 50)
        for name, key in DNS_ENV_BY_SERVER.items():
            self.assertEqual(get_dns_url(name.lower(), environ), environ[key])
            with self.assertRaisesRegex(DNSConfigurationError, key):
                get_dns_url(name, {key: '  '})
        with self.assertRaises(ServerNotFound):
            get_dns_url('unknown', environ)

    async def test_dns_never_prefixes_system_env_and_normalizes_known_services(self):
        with patch.dict(os.environ, {'EXPORT_DNS_APP': 'http://app.example',
                                     'EXPORT_DNS_ACCOUNT': 'http://account.example'}, clear=True):
            api = BaseAPI(header={}, server_name='http://SEEPLN-ACCOUNT')
            self.assertEqual(api.server_name, 'SEEPLN-ACCOUNT')
            self.assertEqual((await api.get_base_url())[2], 'http://account.example/')
            self.assertEqual(normalize_service_target('https://MENU-SERVER1-0'), 'MENU-SERVER1-0')
            self.assertEqual(normalize_service_target('http://real-host'), 'http://real-host')
