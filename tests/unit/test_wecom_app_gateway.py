from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace as N
from unittest.mock import AsyncMock, patch

from httpx import ASGITransport, AsyncClient
from deep_agent.channels.adapters.wecom_app import WecomAppAdapter, MediaHandle
from deep_agent.channels.contracts import InboundMedia
from deep_agent.channels.gateway.app import create_gateway_app
from deep_agent.channels.gateway.webhook import WebhookMessage
from deep_agent.channels.service import ChannelService
from deep_agent.services.wecom_webhook import account_fingerprint


def account():
    return N(id='channel', channel_type='wecom_app', transport='webhook', enabled=True,
             agent_id='agent', session_user_id='owner', webhook_token='fixture',
             webhook_aes_key='fixture', settings={'wecom_app': {'application_id': 'app'}})


def envelope(row):
    fingerprint = account_fingerprint(row)
    return WebhookMessage(message_type='text', message_id='123', request_id='request',
        sender_id='member', peer_id='member', text='hello', payload_hash='a'*64,
        expected_fingerprint=fingerprint, source_metadata={'wecom_app_fingerprint': fingerprint})


class WecomAppGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_final_reply_sends_and_requires_bound_run(self):
        row = account()
        adapter = WecomAppAdapter(row)
        adapter.client = N(wecom_app_send_text=AsyncMock(return_value={'message_id': 'sent'}))
        await adapter.start(None)
        message = envelope(row).message()
        adapter.bind_run(message, 'run')
        await adapter.reply_loading(message, 'reply')
        await adapter.reply(message, 'reply', 'partial', False)
        adapter.client.wecom_app_send_text.assert_not_awaited()
        await adapter.reply(message, 'reply', 'complete', True)
        adapter.client.wecom_app_send_text.assert_awaited_once_with(row, 'run', 'complete')
        adapter.release_run(message)
        with self.assertRaises(Exception):
            await adapter.reply(message, 'reply', 'late', True)
        await adapter.stop()

    async def test_media_bridge_uses_bound_run_and_normalizes_voice(self):
        row = account()
        adapter = WecomAppAdapter(row)
        adapter.client = N(wecom_app_media=AsyncMock(return_value=(b'#!AMR\nfixture', 'voice.amr')))
        await adapter.start(None)
        message = envelope(row).message()
        adapter.bind_run(message, 'run')
        media = InboundMedia('voice', 'voice.amr', 'audio/amr', MediaHandle('123', 'media'))
        with patch('deep_agent.channels.adapters.wecom_app._amr_to_wav', AsyncMock(return_value=b'WAV')):
            data, filename = await adapter.download_media(media)
        self.assertEqual((data, filename), (b'WAV', 'voice.wav'))
        adapter.client.wecom_app_media.assert_awaited_once_with(row, 'run', 'media')
        await adapter.stop()

    async def test_strict_ingress_failure_propagates(self):
        row = account()
        service = ChannelService(N(get_account=AsyncMock(side_effect=RuntimeError('db unavailable'))), N())
        with self.assertRaises(RuntimeError):
            await service.receive(row, N(), envelope(row).message(), strict=True)
        self.assertEqual(service._receivers, {})

    async def test_duplicate_has_result_but_conflict_raises(self):
        row = account()
        previous = N(id='existing', payload_hash='a'*64)
        store = N(get_account=AsyncMock(return_value=row), get_message=AsyncMock(return_value=previous),
                  can_retry=lambda _: False)
        service = ChannelService(store, N())
        result = await service.receive(row, N(), envelope(row).message(), strict=True)
        self.assertEqual(result, {'status': 'duplicate', 'channel_run_id': 'existing'})
        previous.payload_hash = 'b'*64
        with self.assertRaises(ValueError):
            await service.receive(row, N(), envelope(row).message(), strict=True)

    async def test_new_message_only_acknowledges_after_queueing(self):
        row = account()
        store = N(get_account=AsyncMock(return_value=row), get_message=AsyncMock(return_value=None),
                  ensure_binding=AsyncMock(return_value=N(id='binding')),
                  claim_message=AsyncMock(return_value=N(claimed=True, run=N(id='run'))),
                  update_run=AsyncMock())
        adapter = N(new_reply_id=lambda: 'reply', bind_run=lambda *args: None)
        service = ChannelService(store, N())
        with patch.object(service, '_kick'):
            result = await service.receive(row, adapter, envelope(row).message(), strict=True)
        self.assertEqual(result['status'], 'accepted')
        self.assertEqual(len(service.peers[('channel', 'single', 'member')].pending), 1)
        store.update_run.assert_awaited_once_with('run', processing_status='queued')

    async def test_control_ack_does_not_wait_for_cancellation(self):
        row = account()
        store = N(get_account=AsyncMock(return_value=row), get_message=AsyncMock(return_value=None),
                  ensure_binding=AsyncMock(return_value=N(id='binding')),
                  claim_message=AsyncMock(return_value=N(claimed=True, run=N(id='run'))),
                  update_run=AsyncMock())
        adapter = N(new_reply_id=lambda: 'reply', bind_run=lambda *args: None,
                    release_run=lambda *args: None)
        service = ChannelService(store, N())
        started, release = asyncio.Event(), asyncio.Event()
        async def control(*args):
            started.set()
            await release.wait()
        body = envelope(row)
        body.text = '/stop'
        with patch.object(service, '_control', control):
            result = await service.receive(row, adapter, body.message(), strict=True)
            self.assertEqual(result['status'], 'accepted')
            await started.wait()
            peer = service.peers[('channel', 'single', 'member')]
            self.assertEqual(peer.controls, 1)
            tasks = list(service._receivers)
            release.set()
            await asyncio.gather(*tasks)
        self.assertEqual(peer.controls, 0)
        self.assertFalse(service._receivers)

    async def test_private_endpoint_requires_auth_and_reports_failure(self):
        app = create_gateway_app()
        runtime = N(receive_webhook=AsyncMock(side_effect=RuntimeError('offline')))
        app.state.gateway = N(config=N(internal_token='fixture-token'), runtime=runtime)
        async with AsyncClient(transport=ASGITransport(app=app), base_url='http://test') as client:
            body = envelope(account()).model_dump()
            response = await client.post('/accounts/channel/webhook', json=body)
            self.assertEqual(response.status_code, 401)
            response = await client.post('/accounts/channel/webhook', json=body,
                                         headers={'Authorization': 'Bearer fixture-token'})
            self.assertEqual(response.status_code, 503)

    def test_envelope_rejects_group_impersonation(self):
        body = envelope(account())
        body.peer_id = 'other-member'
        with self.assertRaises(ValueError):
            body.message()

    async def test_raw_speex_is_explicitly_rejected(self):
        from deep_agent.channels.adapters.wecom_app import _amr_to_wav, WecomVoiceError
        with self.assertRaisesRegex(WecomVoiceError, '封装格式暂不支持'):
            await _amr_to_wav(b'Speex   raw-frames-without-ogg-container')

    async def test_conflict_is_preserved_only_for_webhook_calls(self):
        from deep_agent.services.channel_gateway_client import (
            ChannelGatewayClient, ChannelGatewayUnavailableError, ChannelWebhookConflictError)
        class Context:
            def __init__(self, value): self.value = value
            async def __aenter__(self): return self.value
            async def __aexit__(self, *args): return False
        response = N(status=409, json=AsyncMock(return_value={'detail': 'conflict'}))
        session = N(request=lambda *args, **kwargs: Context(response))
        client = ChannelGatewayClient()
        client.base_url = 'http://fixture'
        with patch('deep_agent.services.channel_gateway_client.aiohttp.ClientSession',
                   return_value=Context(session)):
            with self.assertRaises(ChannelWebhookConflictError):
                await client.receive_webhook('channel', {})
            with self.assertRaises(ChannelGatewayUnavailableError):
                await client.statuses()

    def test_capabilities_do_not_claim_outbound_media(self):
        caps = WecomAppAdapter.message_capabilities()
        self.assertEqual(caps.kinds, ('text',))
        self.assertFalse(caps.supports_reply)
        self.assertFalse(caps.media_fallback_to_file)
