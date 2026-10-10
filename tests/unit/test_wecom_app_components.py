from __future__ import annotations

import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock

from aiohttp import web

from deep_agent.channels.adapters.wecom_app import WecomAppAdapter
from deep_agent.channels.outbound import PreparedComponent
from deep_agent.services.wecom_client import WecomClient, WecomError, validate_outbound_media


class ApplicationComponentTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = WecomClient()
        self.client.token = AsyncMock(return_value='fixture')
        self.app = N(id='app', revision=1, corp_id='corp', app_secret='fixture', wecom_agent_id='100')
        self.addAsyncCleanup(self.client.close)

    async def test_real_http_uploads_and_structured_messages(self):
        uploads, messages = [], []
        async def upload(request):
            field = await (await request.multipart()).next()
            uploads.append((request.query['type'], field.name, field.filename, bytes(await field.read())))
            return web.json_response({'type': request.query['type'], 'media_id': f'media{len(uploads)}', 'created_at': '1'})
        async def send(request):
            messages.append(await request.json())
            return web.json_response({'errcode': 0, 'msgid': str(len(messages))})
        server = web.Application()
        server.router.add_post('/media/upload', upload)
        server.router.add_post('/message/send', send)
        runner = web.AppRunner(server, access_log=None)
        await runner.setup()
        try:
            site = web.TCPSite(runner, '127.0.0.1', 0)
            await site.start()
            self.client.BASE_URL = f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}'
            import io
            from PIL import Image
            image = io.BytesIO()
            Image.new('RGB', (2, 2)).save(image, format='PNG')
            def box(kind, body):
                return (8 + len(body)).to_bytes(4, 'big') + kind + body
            video = box(b'ftyp', b'mp42' + bytes(4)) + box(b'moov', box(b'trak', box(b'mdia', box(b'hdlr', bytes(8) + b'vide'))))
            samples = {'image': image.getvalue(), 'file': b'report',
                       'audio': b'#!AMR\n' + b'\x04' + bytes(12), 'video': video}
            guard = AsyncMock()
            for kind, data in samples.items():
                result = await self.client.send_component(self.app, 'member', kind, data=data,
                                                         filename='../sample.bin', before_send=guard)
                self.assertEqual(result['status'], 'sent')
            cards = {'textcard': {'title': '报告', 'description': '已完成', 'url': 'https://example.com/report'},
                     'news': {'articles': [{'title': '图文', 'url': 'https://example.com/news', 'picurl': 'https://example.com/p.png'}]}}
            for kind, payload in cards.items():
                await self.client.send_component(self.app, 'member', kind, payload=payload, before_send=guard)
            self.assertEqual([m['msgtype'] for m in messages], ['image', 'file', 'voice', 'video', 'textcard', 'news'])
            self.assertEqual(guard.await_count, 10)
            for body in messages:
                self.assertEqual((body['touser'], body['agentid']), ('member', 100))
            for native, field, filename, data in uploads:
                self.assertEqual((field, filename), ('media', 'sample.bin'))
                self.assertEqual(data, samples['audio' if native == 'voice' else native])
        finally:
            await runner.cleanup()

    async def test_configuration_change_after_upload_prevents_send(self):
        self.client._request = AsyncMock(return_value={'media_id': 'media'})
        guard = AsyncMock(side_effect=[None, ValueError('changed')])
        with self.assertRaisesRegex(ValueError, 'changed'):
            await self.client.send_component(self.app, 'member', 'file', data=b'content', filename='a.txt', before_send=guard)
        self.assertEqual(self.client._request.await_count, 1)
        self.assertEqual(self.client._request.await_args.args[1], '/media/upload')

    async def test_upload_retries_only_explicit_token_expiry(self):
        self.client._request = AsyncMock(side_effect=[{'errcode': 42001}, {'media_id': 'media'}])
        guard = AsyncMock()
        self.assertEqual(await self.client.upload_media(self.app, 'file', b'content', 'x', before_send=guard), 'media')
        calls = self.client._request.await_args_list
        self.assertIsNot(calls[0].kwargs['multipart'], calls[1].kwargs['multipart'])
        self.assertEqual(guard.await_count, 2)
        self.client._request = AsyncMock(side_effect=WecomError('upload', 'network_error'))
        with self.assertRaises(WecomError):
            await self.client.upload_media(self.app, 'file', b'content', 'x')
        self.assertEqual(self.client._request.await_count, 1)

    def test_media_signatures_sizes_and_amr_duration(self):
        for kind, data in [('file', b''), ('audio', b'#!AMR\n'), ('image', b'GIF89a'),
                           ('video', b'\x00\x00\x00\x18ftypqt  '), ('audio', b'#!AMR\n\x04'),
                           ('audio', b'#!AMR\n' + (b'\x04' + bytes(12)) * 3001),
                           ('file', bytes(20 * 1024 * 1024 + 1))]:
            with self.subTest(kind=kind, length=len(data)), self.assertRaises(WecomError):
                validate_outbound_media(kind, data)
        validate_outbound_media('audio', b'#!AMR\n' + (b'\x04' + bytes(12)) * 3000)

    async def test_adapter_falls_back_long_or_invalid_native_media_to_file(self):
        adapter = WecomAppAdapter(N(id='channel'))
        adapter.client = N(wecom_app_send_component=AsyncMock(return_value={'message_id': 'sent'}))
        await adapter.start(None)
        self.addAsyncCleanup(adapter.stop)
        target = N(message_id='message')
        adapter.bind_run(target, 'run')
        for kind, data in [('audio', b'#!AMR\n' + (b'\x04' + bytes(12)) * 3001), ('image', b'GIF89a')]:
            receipt = await adapter.send_component(target, PreparedComponent(kind, data=data, filename='fixture'))
            self.assertEqual(receipt.kind, 'file')
            self.assertEqual(adapter.client.wecom_app_send_component.await_args.args[2].kind, 'file')

    async def test_gateway_serializes_media_and_structured_payload_without_routing(self):
        import base64
        from deep_agent.channels.gateway.deepagent_client import DeepAgentChannelClient
        client = DeepAgentChannelClient(N())
        client._wecom_app_request = AsyncMock(return_value={'status': 'sent'})
        media = PreparedComponent('file', data=b'content', filename='test.txt')
        await client.wecom_app_send_component(N(id='channel'), 'run', media)
        body = client._wecom_app_request.await_args.args[2]
        self.assertEqual(base64.b64decode(body['data_base64']), b'content')
        self.assertEqual(set(body), {'channel_run_id', 'kind', 'data_base64', 'filename'})
        payload = {'title':'结果', 'description':'完成', 'url':'https://example.com'}
        await client.wecom_app_send_component(N(id='channel'), 'run', PreparedComponent('textcard', payload=payload))
        self.assertEqual(client._wecom_app_request.await_args.args[2],
                         {'channel_run_id':'run', 'kind':'textcard', 'payload':payload})

    async def test_invalid_card_does_not_make_network_call(self):
        self.client.call = AsyncMock()
        with self.assertRaises(WecomError) as error:
            await self.client.send_component(self.app, 'member', 'textcard',
                                            payload={'title':'x', 'description':'x', 'url':'javascript:bad'})
        self.assertEqual(error.exception.code, 'invalid_payload')
        self.client.call.assert_not_awaited()

    async def test_cancelled_upload_never_sends(self):
        import asyncio
        self.client._request = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.client.send_component(self.app, 'member', 'file', data=b'content', filename='x.txt')
        self.assertEqual(self.client._request.await_count, 1)
        self.assertEqual(self.client._request.await_args.args[1], '/media/upload')

    async def test_ambiguous_send_failure_is_not_retried(self):
        self.client._request = AsyncMock(side_effect=[{'media_id':'media'}, WecomError('send', 'network_error')])
        with self.assertRaises(WecomError):
            await self.client.send_component(self.app, 'member', 'file', data=b'content', filename='x.txt')
        self.assertEqual(self.client._request.await_count, 2)
