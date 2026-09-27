import asyncio
import logging
import unittest

import httpx

from deep_agent.services.channel_registration import RegistrationError
from deep_agent.services.wecom_registration import WecomRegistrationService


class WecomRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.polls = 0
        self.url = 'https://work.weixin.qq.com/ai/qc/c?s=qr-fixture'
        self.bot_info = {'botid': 'bot-fixture', 'secret': 'secret-fixture'}
        self.service = WecomRegistrationService(transport=httpx.MockTransport(self.handler), poll_interval=.001)

    def handler(self, request):
        self.requests.append(request)
        if request.url.path.endswith('/generate'):
            return httpx.Response(200, json={'data': {'scode': 'scode-fixture', 'auth_url': self.url}})
        self.polls += 1
        return httpx.Response(200, json={'data': {'status': 'init' if self.polls == 1 else 'success',
                                                 'bot_info': self.bot_info}})

    async def asyncTearDown(self):
        await self.service.aclose()

    async def start(self):
        key = (await self.service.start('admin'))['registration_id']
        for _ in range(100):
            if self.service.snapshot(key, 'admin')['status'] not in {'starting', 'waiting'}:
                break
            await asyncio.sleep(.002)
        return key

    async def test_protocol_private_credentials_and_single_consumption(self):
        key = await self.start()
        snapshot = self.service.snapshot(key, 'admin')
        self.assertEqual(snapshot['status'], 'authorized')
        self.assertEqual(snapshot['app_id'], 'bot-fixture')
        self.assertIsNone(snapshot['verification_url'])
        self.assertNotIn('secret-fixture', str(snapshot))
        self.assertNotIn('scode-fixture', str(snapshot))
        self.assertEqual(dict(self.requests[0].url.params)['source'], 'deep-agent')
        self.assertEqual(set(self.requests[0].url.params), {'source', 'plat'})
        self.assertEqual(dict(self.requests[1].url.params), {'scode': 'scode-fixture'})
        with self.assertRaises(RegistrationError):
            self.service.snapshot(key, 'other')
        with self.assertRaises(RuntimeError):
            async with self.service.credentials(key, 'admin'):
                raise RuntimeError('failed save')
        async with self.service.credentials(key, 'admin') as credentials:
            self.assertEqual(credentials, ('bot-fixture', 'secret-fixture'))
        with self.assertRaises(RegistrationError):
            async with self.service.credentials(key, 'admin'):
                pass

    async def test_untrusted_urls_and_incomplete_credentials_fail_closed(self):
        for url in ('http://work.weixin.qq.com/ai/qc/c', 'https://evil.invalid/ai/qc/c',
                    'https://u:p@work.weixin.qq.com/ai/qc/c',
                    'https://work.weixin.qq.com:444/ai/qc/c',
                    'https://work.weixin.qq.com/not-authorize',
                    'https://work.weixin.qq.com/ai/qc/c\n?x=1'):
            self.url = url
            key = await self.start()
            self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'failed', url)
            self.assertIsNone(self.service.entries[key].verification_url)
        self.url = 'https://work.weixin.qq.com/ai/qc/c?s=fixture'
        for info in ({}, {'botid': 'x'}, {'botid': ' ', 'secret': 'private'},
                     {'botid': 'x' * 192, 'secret': 'private'}):
            self.bot_info = info
            key = await self.start()
            self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'failed')
            self.assertNotIn('private', str(self.service.snapshot(key, 'admin')))

    async def test_timeout_cancel_and_shutdown_erase_secrets(self):
        key = await self.start()
        await self.service.cancel(key, 'admin')
        self.assertIsNone(self.service.entries[key].secret)
        self.assertEqual(self.service.entries[key].status, 'cancelled')
        self.service.ttl = .02
        key = await self.start()
        await asyncio.sleep(.03)
        self.assertEqual(self.service.entries[key].status, 'expired')
        self.assertIsNone(self.service.entries[key].secret)
        self.service.ttl = 60
        await self.start()
        await self.service.aclose()
        self.assertFalse(self.service.entries)

    async def test_redirect_is_not_followed_and_errors_are_redacted(self):
        calls = []
        def redirect(request):
            calls.append(request)
            return httpx.Response(302, headers={'location': 'https://evil.invalid/secret'})
        self.service.transport = httpx.MockTransport(redirect)
        key = await self.start()
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'failed')
        self.assertNotIn('evil', str(self.service.snapshot(key, 'admin')))

    async def test_request_logger_does_not_expose_poll_credential(self):
        logger = logging.getLogger('httpx')
        record = logging.LogRecord('httpx', logging.INFO, __file__, 1,
                                   'HTTP Request: GET %s', ('https://work.weixin.qq.com/ai/qc/query_result?scode=private',), None)
        self.assertFalse(logger.filter(record))
        record.args = ('https://example.com/public',)
        self.assertTrue(logger.filter(record))

    async def test_pending_network_is_bounded_and_cancelled(self):
        closed = asyncio.Event()
        async def hang(request):
            try:
                await asyncio.Event().wait()
            finally:
                closed.set()
        self.service.transport = httpx.MockTransport(hang)
        self.service.ttl = .02
        key = (await self.service.start('admin'))['registration_id']
        await asyncio.wait_for(self.service.entries[key].task, 1)
        self.assertTrue(closed.is_set())
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'expired')
        self.service.ttl = 60
        closed.clear()
        key = (await self.service.start('admin'))['registration_id']
        await asyncio.sleep(0)
        await self.service.cancel(key, 'admin')
        self.assertTrue(closed.is_set())
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'cancelled')
