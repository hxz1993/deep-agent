from __future__ import annotations

import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

from deep_agent.services.wecom_client import WecomClient, WecomError, _Token


class _Content:
    def __init__(self, chunks):
        self.chunks = chunks
        self.read_count = 0

    async def iter_chunked(self, _):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk


class _Response:
    def __init__(self, chunks, *, mime='image/png', filename=None, length=None, status=200):
        self.content = _Content(chunks)
        self.content_type = mime
        self.content_disposition = SimpleNamespace(filename=filename) if filename else None
        self.content_length = length
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


class WecomApplicationClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = WecomClient()
        self.app = SimpleNamespace(id='app', revision=2, corp_id='corp',
                                   app_secret='fixture-secret', wecom_agent_id='100')

    async def asyncTearDown(self):
        await self.client.close()

    def media_responses(self, *responses):
        session = SimpleNamespace(get=Mock(side_effect=responses), close=AsyncMock())
        self.client._session = session
        self.client.token = AsyncMock(return_value='fixture-token')
        return session

    async def test_send_splits_utf8_without_losing_characters(self):
        text = '中' * 682 + '😀' + 'a' * 2048 + '尾'
        self.client.call = AsyncMock(side_effect=[{'msgid': str(i)} for i in range(3)])
        result = await self.client.send_message(self.app, 'member', text, message_type='text')
        calls = self.client.call.await_args_list
        chunks = [call.kwargs['body']['text']['content'] for call in calls]
        self.assertEqual(''.join(chunks), text)
        self.assertTrue(all(0 < len(chunk.encode('utf-8')) <= 2048 for chunk in chunks))
        self.assertEqual(result, {'status': 'sent', 'message_id': '2', 'part_count': 3})
        for call in calls:
            self.assertEqual(call.args[1:3], ('POST', '/message/send'))
            self.assertEqual(call.kwargs['body']['touser'], 'member')
            self.assertEqual(call.kwargs['body']['agentid'], 100)

    async def test_real_http_message_types_and_recipient(self):
        from aiohttp import web
        received = []
        async def handler(request):
            received.append(await request.json())
            return web.json_response({'errcode': 0, 'msgid': str(len(received))})
        server = web.Application()
        server.router.add_post('/message/send', handler)
        runner = web.AppRunner(server, access_log=None)
        await runner.setup()
        try:
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]
            self.client.BASE_URL = f'http://127.0.0.1:{port}'
            self.client.token = AsyncMock(return_value='fixture-token')
            await self.client.send_message(self.app, 'member', '已收到，正在处理，请稍候。', message_type='text')
            await self.client.send_message(self.app, 'member', '# 结果\n\n**金额**：[查看](https://example.com)', message_type='markdown')
            self.assertEqual([body['msgtype'] for body in received], ['text', 'markdown'])
            self.assertIn('**结果**', received[1]['markdown']['content'])
            self.assertIn('[查看](https://example.com)', received[1]['markdown']['content'])
            for body in received:
                self.assertEqual(body['touser'], 'member')
                self.assertEqual(body['agentid'], 100)
                self.assertLessEqual(len(body[body['msgtype']]['content'].encode()), 2048)
        finally:
            await runner.cleanup()

    async def test_send_stops_at_first_failed_part(self):
        self.client.call = AsyncMock(side_effect=[{'msgid': 'first'}, WecomError('send', 60020)])
        with self.assertRaises(WecomError) as error:
            await self.client.send_message(self.app, 'member', 'a' * 6144, message_type='text')
        self.assertEqual(error.exception.code, '60020')
        self.assertEqual(self.client.call.await_count, 2)

    async def test_invalid_or_unlicensed_recipient_is_not_success(self):
        for field in ('invaliduser', 'unlicenseduser'):
            self.client.call = AsyncMock(return_value={field: 'member', 'errcode': 0})
            with self.assertRaises(WecomError) as error:
                await self.client.send_message(self.app, 'member', 'a' * 4096, message_type='text')
            self.assertEqual(error.exception.code, 'recipient_unavailable')
            self.assertEqual(self.client.call.await_count, 1)

    async def test_media_declared_and_streamed_size_limits(self):
        declared = _Response([b'abc'], length=11)
        self.media_responses(declared)
        with self.assertRaises(WecomError) as error:
            await self.client.media(self.app, 'media', limit=10)
        self.assertEqual(error.exception.code, 'size_limit')
        self.assertEqual(declared.content.read_count, 0)
        streamed = _Response([b'123456', b'123456', b'not-read'])
        self.media_responses(streamed)
        with self.assertRaises(WecomError) as error:
            await self.client.media(self.app, 'media', limit=10)
        self.assertEqual(error.exception.code, 'size_limit')
        self.assertEqual(streamed.content.read_count, 2)

    async def test_media_token_error_retries_once_and_clears_only_used_token(self):
        session = self.media_responses(
            _Response([b'{"errcode":42001}'], mime='application/json'),
            _Response([b'image'], filename='image.png'))
        self.client._tokens[('app', 2)] = _Token('old', time.monotonic() + 999)
        self.client.token = AsyncMock(side_effect=['old', 'new'])
        result = await self.client.media(self.app, 'media', limit=100)
        self.assertEqual(result, (b'image', 'image.png', 'image/png'))
        self.assertNotIn(('app', 2), self.client._tokens)
        self.assertEqual(session.get.call_count, 2)
        for call in session.get.call_args_list:
            self.assertEqual(call.args[0], self.client.BASE_URL + '/media/get')
            self.assertFalse(call.kwargs['allow_redirects'])
            self.assertEqual(call.kwargs['params']['media_id'], 'media')
        self.assertEqual(session.get.call_args_list[1].kwargs['params']['access_token'], 'new')

    async def test_media_error_json_is_not_a_file_or_leaked_error(self):
        session = self.media_responses(_Response(
            [b'{"errcode":40007,"errmsg":"private-media-secret"}'], mime='application/json'))
        with self.assertRaises(WecomError) as error:
            await self.client.media(self.app, 'media', limit=100)
        self.assertEqual(error.exception.code, '40007')
        self.assertNotIn('private-media-secret', str(error.exception))
        self.assertEqual(session.get.call_count, 1)

    async def test_media_json_attachment_remains_file(self):
        content = b'{"errcode":42001,"data":"customer document"}'
        self.media_responses(_Response([content], mime='application/json', filename='report.json'))
        self.assertEqual(await self.client.media(self.app, 'media', limit=100),
                         (content, 'report.json', 'application/json'))

    async def test_media_empty_and_redirect_rejected(self):
        for response, expected in [(_Response([]), 'empty_media'), (_Response([], status=302), 'http_302')]:
            self.media_responses(response)
            with self.assertRaises(WecomError) as error:
                await self.client.media(self.app, 'media', limit=100)
            self.assertEqual(error.exception.code, expected)

    async def test_send_rechecks_after_token_wait_and_before_each_part(self):
        enabled = True
        order = []
        async def token(app):
            order.append('token')
            return 'token'
        async def guard():
            order.append('guard')
            if not enabled:
                raise WecomError('send', 'configuration_changed')
        async def send(*args, **kwargs):
            nonlocal enabled
            order.append('send')
            enabled = False
            return {'errcode': 0, 'msgid': 'first'}
        self.client.token = AsyncMock(side_effect=token)
        self.client._request = AsyncMock(side_effect=send)
        with self.assertRaises(WecomError) as error:
            await self.client.send_message(self.app, 'member', 'a' * 6144, message_type='text', before_send=guard)
        self.assertEqual(error.exception.code, 'configuration_changed')
        self.assertEqual(self.client._request.await_count, 1)
        self.assertEqual(order, ['token', 'guard', 'send', 'token', 'guard'])

    async def test_send_rechecks_again_after_expired_token_retry(self):
        self.client.token = AsyncMock(side_effect=['old', 'new'])
        self.client._request = AsyncMock(return_value={'errcode': 42001})
        guard = AsyncMock(side_effect=[None, WecomError('send', 'configuration_changed')])
        with self.assertRaises(WecomError) as error:
            await self.client.send_message(self.app, 'member', 'hello', message_type='text', before_send=guard)
        self.assertEqual(error.exception.code, 'configuration_changed')
        self.assertEqual(self.client._request.await_count, 1)
        self.assertEqual(guard.await_count, 2)
