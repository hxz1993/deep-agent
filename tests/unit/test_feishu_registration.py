import asyncio
import time
import unittest

from deep_agent.services.feishu_registration import FeishuRegistrationService, RegistrationError


class FeishuRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        async def register(**kwargs):
            kwargs['on_qr_code']({'url': 'https://open.feishu.cn/page/cli?user_code=fixture', 'expire_in': 60})
            return {'client_id': 'cli_fixture', 'client_secret': 'fixture-secret'}
        self.service = FeishuRegistrationService(register=register)

    async def asyncTearDown(self):
        await self.service.aclose()

    async def authorized(self):
        data = await self.service.start('admin')
        await asyncio.sleep(0)
        return data['registration_id']

    async def test_app_name_is_trimmed_snapshotted_and_passed_to_sdk(self):
        names = []
        async def register(**kwargs):
            names.append(kwargs['app_preset']['name'])
            return {'client_id': 'cli_fixture', 'client_secret': 'fixture-secret'}
        self.service.register = register
        first = await self.service.start('admin', app_name='  财务助手  ')
        await asyncio.sleep(0)
        await self.service.start('other', app_name='   ')
        await asyncio.sleep(0)
        self.assertEqual(names, ['财务助手', 'DeepAgent'])
        self.assertEqual(self.service.entries[first['registration_id']].app_name, '财务助手')

    async def test_registration_uses_default_template_with_name_only(self):
        captured = {}
        async def register(**kwargs):
            captured.update(kwargs)
            return {'client_id': 'cli_fixture', 'client_secret': 'fixture-secret'}
        self.service.register = register
        await self.authorized()
        self.assertEqual(set(captured), {'on_qr_code', 'on_status_change', 'app_preset'})
        self.assertEqual(captured['app_preset'], {'name': 'DeepAgent'})

        # Exercise the real SDK URL builder so custom lists cannot silently return.
        from urllib.parse import parse_qs, urlsplit
        from lark_oapi.scene.registration import _RegistrationFlow
        flow = _RegistrationFlow(**captured, source=None, domain='https://accounts.feishu.cn',
                                 lark_domain='https://accounts.larksuite.com')
        url = flow._build_qr_url('https://open.feishu.cn/page/cli?user_code=fixture')
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(query['name'], ['DeepAgent'])
        self.assertEqual(query['user_code'], ['fixture'])
        for key in ('addons', 'desc', 'createOnly', 'clientID'):
            self.assertNotIn(key, query)
        self.assertLess(len(url.encode()), 150)

    async def test_credentials_private_owner_bound_and_one_time(self):
        key = await self.authorized()
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'authorized')
        self.assertNotIn('fixture-secret', str(self.service.snapshot(key, 'admin')))
        with self.assertRaises(RegistrationError):
            self.service.snapshot(key, 'other')
        with self.assertRaises(RuntimeError):
            async with self.service.credentials(key, 'admin'):
                raise RuntimeError('database unavailable')
        async with self.service.credentials(key, 'admin') as credentials:
            self.assertEqual(credentials, ('cli_fixture', 'fixture-secret'))
        with self.assertRaises(RegistrationError):
            async with self.service.credentials(key, 'admin'):
                pass
        self.assertIsNone(self.service.entries[key].secret)

    async def test_cancel_expiry_capacity_shutdown(self):
        key = await self.authorized()
        await self.service.cancel(key, 'admin')
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'cancelled')
        key = await self.authorized()
        self.service.entries[key].deadline = time.monotonic() - 1
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'expired')
        self.assertIsNone(self.service.entries[key].secret)
        self.service.capacity = 1
        await self.service.start('other')
        self.assertEqual(len(self.service.entries), 1)
        await self.service.aclose()
        self.assertFalse(self.service.entries)

    async def test_failure_redacts_exception_and_validates_url(self):
        async def fail(**kwargs):
            raise RuntimeError('secret=private')
        self.service.register = fail
        key = await self.authorized()
        data = self.service.snapshot(key, 'admin')
        self.assertEqual(data['status'], 'failed')
        self.assertNotIn('private', str(data))
        async def bad_url(**kwargs):
            kwargs['on_qr_code']({'url': 'https://attacker.invalid/'})
        self.service.register = bad_url
        key = await self.authorized()
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'failed')

    async def test_timeout_and_pending_cancel(self):
        async def wait(**kwargs):
            kwargs['on_qr_code']({'url': 'https://open.feishu.cn/page/cli', 'expire_in': 60})
            await asyncio.Event().wait()
        self.service.register = wait
        self.service.ttl = .01
        key = await self.authorized()
        await asyncio.sleep(.02)
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'expired')
        self.service.ttl = 60
        key = await self.authorized()
        await self.service.cancel(key, 'admin')
        self.assertTrue(self.service.entries[key].task.done())

    async def test_denied_and_sdk_expiry_have_safe_terminal_status(self):
        for exception_name, expected in [('AppAccessDeniedError', 'denied'), ('AppExpiredError', 'expired')]:
            async def fail(**kwargs):
                raise type(exception_name, (Exception,), {})('private remote response')
            self.service.register = fail
            key = await self.authorized()
            data = self.service.snapshot(key, 'admin')
            self.assertEqual(data['status'], expected)
            self.assertNotIn('private', str(data))

    async def test_owner_limit_and_cancel_during_wait(self):
        async def wait(**kwargs):
            await asyncio.Event().wait()
        self.service.register = wait
        first = await self.service.start('admin')
        await self.service.start('admin')
        with self.assertRaises(RegistrationError) as error:
            await self.service.start('admin')
        self.assertEqual(error.exception.status_code, 429)
        with self.assertRaises(RegistrationError):
            await self.service.cancel(first['registration_id'], 'other')
        await self.service.cancel(first['registration_id'], 'admin')
        await self.service.start('admin')

    async def test_real_official_sdk_device_flow_with_transport_fixture(self):
        import httpx
        from urllib.parse import parse_qs
        from unittest.mock import patch
        from deep_agent.services.feishu_registration import register_with_sdk
        actions = []
        def handler(request):
            action = parse_qs(request.content.decode())['action'][0]
            actions.append(action)
            payload = {
                'init': {'supported_auth_methods': ['client_secret']},
                'begin': {'device_code': 'device-fixture', 'interval': 0, 'expires_in': 60,
                          'verification_uri_complete': 'https://open.feishu.cn/page/cli?user_code=fixture'},
                'poll': {'client_id': 'cli_fixture', 'client_secret': 'fixture-secret'},
            }[action]
            return httpx.Response(200, json=payload)
        original_client = httpx.AsyncClient
        def client(*args, **kwargs):
            return original_client(*args, **kwargs, transport=httpx.MockTransport(handler))
        self.service.register = register_with_sdk
        with patch('lark_oapi.scene.registration.httpx.AsyncClient', client):
            key = (await self.service.start('admin'))['registration_id']
            for _ in range(100):
                if self.service.snapshot(key, 'admin')['status'] == 'authorized':
                    break
                await asyncio.sleep(.01)
            self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'authorized')
        self.assertEqual(actions, ['init', 'begin', 'poll'])

    async def test_authorized_credentials_expire_without_polling(self):
        self.service.ttl = .02
        key = await self.authorized()
        entry = self.service.entries[key]
        await asyncio.sleep(.03)
        self.assertEqual(entry.status, 'expired')
        self.assertIsNone(entry.secret)
        self.assertTrue(entry.task.done())
        # Starting another task reclaims the expired slot.
        self.service.capacity = 1
        self.service.ttl = 60
        await self.service.start('other')
        self.assertNotIn(key, self.service.entries)

    async def test_registration_rejects_partial_credentials_and_foreign_domain(self):
        async def partial(**kwargs):
            return {'client_id': 'cli_fixture'}
        self.service.register = partial
        key = await self.authorized()
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'failed')
        async def foreign(**kwargs):
            kwargs['on_status_change']({'status': 'polling'})
            kwargs['on_status_change']({'status': 'domain_switched'})
        self.service.register = foreign
        key = await self.authorized()
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'failed')

    async def test_two_concurrent_saves_cannot_reuse_authorization(self):
        key = await self.authorized()
        claimed = asyncio.Event()
        release = asyncio.Event()
        async def save():
            async with self.service.credentials(key, 'admin'):
                claimed.set()
                await release.wait()
        first = asyncio.create_task(save())
        await claimed.wait()
        second = asyncio.create_task(save())
        await asyncio.sleep(0)
        release.set()
        await first
        with self.assertRaises(RegistrationError):
            await second

    async def test_terminal_registrations_release_capacity_for_retries(self):
        self.service.capacity = 1
        for _ in range(20):
            key = await self.authorized()
            await self.service.cancel(key, 'admin')
        async def fail(**kwargs):
            raise RuntimeError('remote error')
        self.service.register = fail
        for _ in range(20):
            key = await self.authorized()
            self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'failed')
        self.assertEqual(len(self.service.entries), 1)

    async def test_qr_deadline_shortens_running_timeout_without_polling(self):
        async def wait(**kwargs):
            kwargs['on_qr_code']({'url': 'https://open.feishu.cn/page/cli', 'expire_in': 1})
            await asyncio.Event().wait()
        self.service.register = wait
        key = (await self.service.start('admin'))['registration_id']
        entry = self.service.entries[key]
        await asyncio.wait_for(asyncio.shield(entry.task), timeout=2)
        self.assertEqual(entry.status, 'expired')
        self.assertIsNone(entry.verification_url)
