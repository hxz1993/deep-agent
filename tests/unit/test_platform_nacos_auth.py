import asyncio
import unittest
from dataclasses import replace
from unittest.mock import AsyncMock, Mock, patch

from deepfos_platform.lib.nacos_auth import (
    RamRoleAuthProvider, StsCredentials, build_config_sign_fields,
    build_naming_sign_fields, create_auth_provider, hmac_sha1_base64,
)


def response(status=200, text='', payload=None):
    return Mock(status=status, text=AsyncMock(return_value=text),
                json=AsyncMock(return_value=payload), release=Mock())


class NacosSignatureTests(unittest.TestCase):
    def test_source_literal_hmac_vectors(self):
        self.assertEqual(hmac_sha1_base64('hello', 'secret'), 'URIFXAX5RPhXVe/FzYlw4ZTp9Fs=')
        fields = build_naming_sign_fields(access_key='AK_test', secret_key='SK_test',
                                         timestamp_ms=1700000000000)
        self.assertEqual(fields['signature'], 'IiBgus7HHeClOvIIbsuR9/9+npY=')
        fields = build_naming_sign_fields(access_key='AK_test', secret_key='SK_test',
                                         service_name='my-service', group_name='DEFAULT_GROUP',
                                         timestamp_ms=1700000000000)
        self.assertEqual(fields['data'], '1700000000000@@DEFAULT_GROUP@@my-service')
        self.assertEqual(fields['signature'], 'bLMcSZiNQcbBlvvghjm9nVLEtLU=')
        fields = build_config_sign_fields(access_key='AK_test', secret_key='SK_test',
                                         tenant='ns-id', group='DEFAULT_GROUP',
                                         timestamp_ms=1700000000000)
        self.assertEqual(fields['Spas-Signature'], 'xbLYqS0cIOfKznjr4nfhKBez/rE=')

    def test_grouping_and_config_resource_boundaries(self):
        for service, group, tail in (('svc', 'group', '@@group@@svc'),
                                     ('other@@svc', 'group', '@@other@@svc'),
                                     ('svc', '', '@@svc'), ('', 'group', '')):
            with self.subTest(service=service, group=group):
                fields = build_naming_sign_fields(access_key='ak', secret_key='sk',
                                                 service_name=service, group_name=group,
                                                 timestamp_ms=0, security_token='token')
                self.assertEqual(fields['data'], '0' + tail)
                self.assertEqual(fields['Spas-SecurityToken'], 'token')
        for tenant, group, data in (('ns', 'g', 'ns+g+0'), ('ns', '', 'ns+0'),
                                   ('', 'g', 'g+0'), ('', '', '0')):
            with self.subTest(tenant=tenant, group=group):
                fields = build_config_sign_fields(access_key='ak', secret_key='sk',
                                                 tenant=tenant, group=group, timestamp_ms=0)
                self.assertEqual(fields['Spas-Signature'], hmac_sha1_base64(data, 'sk'))
                self.assertNotIn('Spas-SecurityToken', fields)

    def test_factory_rejects_unknown_and_incomplete_modes(self):
        for mode, kwargs in (('invalid', {}), ('aksk', {}), ('aksk', {'access_key': 'ak'}),
                             ('ram', {}), ('ram', {'ram_role_name': ' '})):
            with self.subTest(mode=mode, kwargs=kwargs), self.assertRaises(ValueError):
                create_auth_provider(mode, **kwargs)

    def test_static_enrichment_preserves_business_body(self):
        provider = create_auth_provider('aksk', access_key='ak', secret_key='sk')
        body = {'serviceName': 'svc', 'groupName': 'group'}
        signed = provider.enrich('ns/instance', body)
        self.assertIn('signature', signed)
        self.assertEqual(body, {'serviceName': 'svc', 'groupName': 'group'})
        config = provider.enrich('cs/configs', {'dataId': 'one', 'tenant': 'ns', 'group': 'group'})
        self.assertIn('Spas-Signature', config)
        self.assertNotIn('accessToken', config)
        self.assertEqual(provider.enrich('auth/login', {'password': 'pw'}), {'password': 'pw'})


class NacosProviderTests(unittest.IsolatedAsyncioTestCase):
    async def test_static_and_ram_enrichment_share_signature_contract(self):
        static = create_auth_provider('aksk', access_key='ak', secret_key='sk')
        ram = create_auth_provider('ram', ram_role_name='test-role')
        self.addAsyncCleanup(static.close)
        self.addAsyncCleanup(ram.close)
        with patch('deepfos_platform.lib.httpcli.AioHttpCli.put', AsyncMock(return_value=response(text='imds-token'))), patch(
            'deepfos_platform.lib.httpcli.AioHttpCli.get', AsyncMock(return_value=response(payload={
                'Code': 'Success', 'AccessKeyId': 'ak', 'AccessKeySecret': 'sk',
                'SecurityToken': 'sts-token', 'Expiration': '2099-01-01T00:00:00Z',
            }))
        ):
            await static.initialize()
            await ram.initialize()
        for endpoint, body in (
            ('ns/instance', {'serviceName': 'svc', 'groupName': 'group'}),
            ('cs/configs', {'dataId': 'one', 'tenant': 'ns', 'group': 'group'}),
            ('auth/login', {'password': 'pw'}), ('other', {'value': 'unchanged'}),
        ):
            with self.subTest(endpoint=endpoint), patch('deepfos_platform.lib.nacos_auth.time.time', return_value=1700000000):
                original = body.copy()
                static_fields = static.enrich(endpoint, body)
                ram_fields = ram.enrich(endpoint, body)
                if endpoint.startswith(('ns/', 'cs/')):
                    self.assertEqual(ram_fields.pop('Spas-SecurityToken'), 'sts-token')
                    self.assertNotIn('Spas-SecurityToken', static_fields)
                self.assertEqual(ram_fields, static_fields)
                self.assertEqual(body, original)
                self.assertIsNot(static_fields, body)
                self.assertIsNot(ram_fields, body)

    async def test_static_refresh_and_initialize_never_login(self):
        provider = create_auth_provider('aksk', access_key='ak', secret_key='sk')
        api = Mock(login=AsyncMock())
        provider.bind(api)
        await provider.initialize()
        self.assertFalse(await provider.refresh())
        api.login.assert_not_awaited()
        await provider.close()

    async def test_password_anonymous_and_owned_renewal_cleanup(self):
        anonymous = create_auth_provider('password')
        api = Mock(_token=None, login=AsyncMock())
        anonymous.bind(api)
        self.assertFalse(await anonymous.refresh())
        api.login.assert_not_awaited()
        provider = create_auth_provider('password', user='user', password='secret')
        provider.bind(api)
        api.login.return_value = {'accessToken': 'token', 'tokenTtl': 3600}
        self.assertTrue(await provider.refresh())
        task = provider._renewal_task
        self.assertIsNotNone(task)
        self.assertEqual(api._token, 'token')
        self.assertEqual(provider.enrich('ns/service/list', {})['accessToken'], 'token')
        await provider.refresh()
        self.assertTrue(task.cancelled())
        task = provider._renewal_task
        await provider.close()
        self.assertTrue(task.cancelled())
        await provider.close()

    async def test_password_close_during_login_cannot_restart_renewal(self):
        started, finish = asyncio.Event(), asyncio.Event()

        async def login(*args):
            started.set()
            await finish.wait()
            return {'accessToken': 'late-token', 'tokenTtl': 3600}

        provider = create_auth_provider('password', user='user', password='secret')
        provider.bind(Mock(_token=None, login=AsyncMock(side_effect=login)))
        pending = asyncio.create_task(provider.refresh())
        await started.wait()
        await provider.close()
        finish.set()
        try:
            self.assertFalse(await pending)
            self.assertIsNone(provider._renewal_task)
        finally:
            await provider.close()

    async def test_password_retries_three_times_and_does_not_schedule_on_demand(self):
        provider = create_auth_provider('password', user='user', password='secret')
        api = Mock(_token=None, login=AsyncMock(side_effect=[RuntimeError('offline'),
                   RuntimeError('offline'), {'accessToken': 'token', 'tokenTtl': 3600}]))
        provider.bind(api)
        with patch('deepfos_platform.lib.nacos_auth.asyncio.sleep', new_callable=AsyncMock):
            self.assertTrue(await provider.refresh(schedule_renewal=False))
        self.assertEqual(api.login.await_count, 3)
        self.assertIsNone(provider._renewal_task)
        await provider.close()

    async def test_password_invalid_payload_has_no_sensitive_error(self):
        provider = create_auth_provider('password', user='user', password='secret')
        provider.bind(Mock(_token=None, login=AsyncMock(return_value={'accessToken': 'sensitive',
                                                                   'tokenTtl': 'bad'})))
        with patch('deepfos_platform.lib.nacos_auth.asyncio.sleep', new_callable=AsyncMock):
            with self.assertRaises(RuntimeError) as caught:
                await provider.refresh()
        self.assertNotIn('sensitive', str(caught.exception))
        self.assertIsNone(provider._renewal_task)
        await provider.close()

    async def test_ram_lazy_locked_cache_and_exact_refresh_boundary(self):
        provider = RamRoleAuthProvider('test-role')
        credentials = StsCredentials('ak', 'sk', 'token', 10000)
        provider._fetch_credentials = AsyncMock(return_value=credentials)
        self.assertIsNone(provider._credentials)
        with patch('deepfos_platform.lib.nacos_auth.time.time', return_value=8000):
            await asyncio.gather(*(provider.prepare_request() for _ in range(10)))
            provider._fetch_credentials.assert_awaited_once()
        with patch('deepfos_platform.lib.nacos_auth.time.time', return_value=9099):
            await provider.prepare_request()
            self.assertEqual(provider._fetch_credentials.await_count, 1)
        with patch('deepfos_platform.lib.nacos_auth.time.time', return_value=9100):
            provider._fetch_credentials.return_value = replace(credentials, expiration=20000)
            await provider.prepare_request()
            self.assertEqual(provider._fetch_credentials.await_count, 2)
            self.assertTrue(await provider.refresh())
            self.assertEqual(provider._fetch_credentials.await_count, 3)
        fields = provider.enrich('ns/instance/list', {'serviceName': 'svc', 'groupName': 'g'})
        self.assertEqual(fields['Spas-SecurityToken'], 'token')
        self.assertIn('signature', fields)
        self.assertNotIn('sk', fields)
        await provider.close()

    async def test_ram_failed_refresh_keeps_previous_credentials(self):
        provider = RamRoleAuthProvider('test-role')
        provider._credentials = StsCredentials('old-ak', 'old-sk', 'old-token', 20000)
        provider._fetch_credentials = AsyncMock(side_effect=RuntimeError('metadata unavailable'))
        with self.assertRaises(RuntimeError):
            await provider.refresh()
        self.assertEqual(provider._credentials.access_key_id, 'old-ak')
        await provider.close()

    async def test_ram_imdsv2_and_v1_fallback_no_redirects(self):
        for token_status in (200, 404):
            with self.subTest(token_status=token_status):
                provider = RamRoleAuthProvider('test role')
                token = response(token_status, text='metadata-token')
                creds = response(payload={'Code': 'Success', 'AccessKeyId': 'ak',
                                          'AccessKeySecret': 'sk', 'SecurityToken': 'token',
                                          'Expiration': '2099-01-01T00:00:00Z'})
                with patch('deepfos_platform.lib.httpcli.AioHttpCli.put', AsyncMock(return_value=token)) as put, patch(
                    'deepfos_platform.lib.httpcli.AioHttpCli.get', AsyncMock(return_value=creds)
                ) as get:
                    await provider.prepare_request()
                    self.assertFalse(put.call_args.kwargs['allow_redirects'])
                    self.assertFalse(get.call_args.kwargs['allow_redirects'])
                    self.assertLessEqual(get.call_args.kwargs['timeout'].total, 5)
                    self.assertTrue(get.call_args.args[0].endswith('/test%20role'))
                    headers = get.call_args.kwargs['headers']
                    self.assertEqual(headers.get('X-aliyun-ecs-metadata-token'),
                                     'metadata-token' if token_status == 200 else None)
                    token.release.assert_called_once()
                    creds.release.assert_called_once()
                await provider.close()

    async def test_ram_rejects_invalid_expired_and_sensitive_payloads(self):
        good = {'Code': 'Success', 'AccessKeyId': 'ak', 'AccessKeySecret': 'sensitive',
                'SecurityToken': 'sensitive-token', 'Expiration': '2099-01-01T00:00:00Z'}
        for updates in ({'Code': 'Denied'}, {'AccessKeyId': ''}, {'SecurityToken': ''},
                        {'Expiration': '2000-01-01T00:00:00Z'}, {'Expiration': 'not-a-date'}):
            with self.subTest(updates=updates):
                provider = RamRoleAuthProvider('role')
                with patch('deepfos_platform.lib.httpcli.AioHttpCli.put', AsyncMock(side_effect=RuntimeError('unavailable'))), patch(
                    'deepfos_platform.lib.httpcli.AioHttpCli.get', AsyncMock(return_value=response(payload={**good, **updates}))
                ):
                    with self.assertRaises(RuntimeError) as caught:
                        await provider.prepare_request()
                    self.assertNotIn('sensitive', str(caught.exception))
                    self.assertIsNone(provider._credentials)
                await provider.close()
