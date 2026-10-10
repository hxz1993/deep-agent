from __future__ import annotations

import os
import socket
import unittest
from unittest.mock import patch

from deepfos_platform.conf.option import OptionValueError
from deepfos_platform.options import PlatformOptions, _resolve_instance_host


class PlatformOptionsTests(unittest.TestCase):
    def test_discovery_enabled_checks_selected_registry_on_assignment(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            for implementation in ('eureka', 'nacos', 'k8s', 'dns', 'local'):
                with self.subTest(implementation=implementation):
                    option = PlatformOptions()
                    with patch('deepfos_platform.options.OPTION', option):
                        option.srvdis.implementation = implementation
                        option.srvdis.enabled = False
                        if implementation in ('dns', 'local'):
                            option.srvdis.enabled = True
                        else:
                            with self.assertRaisesRegex(OptionValueError, implementation):
                                option.srvdis.enabled = True
                            self.assertFalse(option.srvdis.enabled)

    def test_discovery_validates_only_selected_registry(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            for implementation in ('dns', 'local', 'nacos'):
                option = PlatformOptions()
                option.nacos.server = 'http://registry:8848'
                option.srvdis.implementation = implementation
                with patch('deepfos_platform.options.OPTION', option):
                    option.srvdis.enabled = True
                    option.srvdis.validate()

    def test_nacos_missing_address_is_rejected(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            option = PlatformOptions()
            option.srvdis.implementation = 'nacos'
            with patch('deepfos_platform.options.OPTION', option):
                with self.assertRaisesRegex(OptionValueError, 'nacos'):
                    option.srvdis.validate()

    def test_dns_aliases_preserve_legacy_priority(self) -> None:
        with patch.dict(os.environ, {
            'EXPORT_DNS_ACCOUNT': 'https://account.example/api',
            'EXPORT_DNS_WEB_GATEWAY': 'https://gateway.example',
            'EXPORT_APP_SERVER_NAME': 'legacy-app-server',
            'EXPORT_DNS_APP': 'http://app.example',
        }, clear=True):
            option = PlatformOptions()
            self.assertEqual(option.server.account, 'https://account.example/api')
            self.assertEqual(option.server.base, 'https://gateway.example')
            self.assertEqual(option.server.app, 'legacy-app-server')

    def test_empty_legacy_dns_alias_does_not_fall_through(self) -> None:
        with patch.dict(os.environ, {
            'EXPORT_APP_SERVER_NAME': '',
            'EXPORT_DNS_APP': 'http://app.example',
        }, clear=True):
            self.assertEqual(PlatformOptions().server.app, '')

    def test_auth_mode_checks_credentials_on_assignment(self) -> None:
        for mode, key in [('aksk', 'NACOS_ACCESS_KEY'), ('ram', 'RAM_ROLE_NAME')]:
            with self.subTest(mode=mode), patch.dict(os.environ, {}, clear=True):
                option = PlatformOptions()
                with patch('deepfos_platform.options.OPTION', option):
                    with self.assertRaisesRegex(OptionValueError, key):
                        option.nacos.auth_mode = mode
                    self.assertEqual(option.nacos.auth_mode, 'password')

    def test_auth_modes_load_from_environment(self) -> None:
        environments = [
            {'EXPORT_NACOS_AUTH_MODE': 'password'},
            {'EXPORT_NACOS_AUTH_MODE': 'aksk', 'NACOS_ACCESS_KEY': 'test-ak',
             'NACOS_SECRET_KEY': 'test-sk'},
            {'EXPORT_NACOS_AUTH_MODE': 'ram', 'EXPORT_NACOS_RAM_ROLE_NAME': 'test-role'},
        ]
        for environ in environments:
            with self.subTest(mode=environ['EXPORT_NACOS_AUTH_MODE']), patch.dict(os.environ, environ, clear=True):
                option = PlatformOptions()
                with patch('deepfos_platform.options.OPTION', option):
                    option.nacos.validate()
                    self.assertEqual(option.nacos.auth_mode, environ['EXPORT_NACOS_AUTH_MODE'])

    def test_incomplete_credentials_are_rejected(self) -> None:
        for mode, key in [('aksk', 'NACOS_ACCESS_KEY'), ('ram', 'RAM_ROLE_NAME')]:
            with self.subTest(mode=mode), patch.dict(os.environ, {
                'EXPORT_NACOS_AUTH_MODE': mode,
            }, clear=True):
                option = PlatformOptions()
                with patch('deepfos_platform.options.OPTION', option):
                    with self.assertRaisesRegex(OptionValueError, key):
                        self.assertEqual(option.nacos.auth_mode, mode)

    def test_load_env_checks_discovery_and_auth_after_dependencies(self) -> None:
        environments = [
            {'EXPORT_SERVICE_DISCOVERY_IMPLEMENTATION': 'dns'},
            {'EXPORT_SERVICE_DISCOVERY_IMPLEMENTATION': 'nacos',
             'EXPORT_NACOS_SERVER_ADDR': 'http://registry:8848',
             'EXPORT_NACOS_AUTH_MODE': 'aksk', 'NACOS_ACCESS_KEY': 'test-ak',
             'NACOS_SECRET_KEY': 'test-sk'},
            {'EXPORT_SERVICE_DISCOVERY_IMPLEMENTATION': 'nacos',
             'EXPORT_NACOS_SERVER_ADDR': 'http://registry:8848',
             'EXPORT_NACOS_AUTH_MODE': 'ram', 'EXPORT_NACOS_RAM_ROLE_NAME': 'test-role'},
        ]
        for environ in environments:
            with self.subTest(environ=environ), patch.dict(os.environ, {
                'EXPORT_WEB_GATEWAY_SERVER': 'http://gateway.example',
                'EXPORT_EUREKA_SERVER_ADDR': 'http://eureka.example',
                **environ,
            }, clear=True):
                option = PlatformOptions()
                option.srvdis.implementation = 'eureka'
                option.nacos.access_key = ''
                option.nacos.secret_key = ''
                option.nacos.ram_role_name = ''
                with patch('deepfos_platform.options.OPTION', option):
                    os.environ['EXPORT_SERVICE_DISCOVERY_ENABLED'] = 'true'
                    option.load_env()
                    self.assertEqual(option.srvdis.implementation,
                                     environ['EXPORT_SERVICE_DISCOVERY_IMPLEMENTATION'])
                    self.assertTrue(option.srvdis.enabled)
                    self.assertEqual(option.nacos.auth_mode,
                                     environ.get('EXPORT_NACOS_AUTH_MODE', 'password'))

    def test_instance_host_uses_resolved_hostname(self) -> None:
        with (
            patch("deepfos_platform.options.socket.gethostname", return_value="deep-agent.local"),
            patch("deepfos_platform.options.socket.gethostbyname", return_value="192.0.2.10"),
        ):
            self.assertEqual(_resolve_instance_host(), "192.0.2.10")

    def test_instance_host_falls_back_when_hostname_is_not_resolvable(self) -> None:
        with (
            patch("deepfos_platform.options.socket.gethostname", return_value="missing.local"),
            patch(
                "deepfos_platform.options.socket.gethostbyname",
                side_effect=socket.gaierror(8, "nodename nor servname provided"),
            ),
        ):
            self.assertEqual(_resolve_instance_host(), "127.0.0.1")


if __name__ == "__main__":
    unittest.main()
