from __future__ import annotations

import time
import unittest
from unittest.mock import AsyncMock, patch
from types import SimpleNamespace

from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from wechatpy.enterprise.crypto import WeChatCrypto
from deep_agent.api.v1 import api_router
from deep_agent.core.context import AppContext, init_context, reset_context
from deep_agent.schemas.channel import ChannelAccountCreate
from deep_agent.services.wecom_webhook import xml_fields, parse_message, account_fingerprint
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class WebhookApiTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, factory = await make_sqlite_stores()
        agent_id = await seed_default_agent(self.stores)
        self.app_record = await self.stores.wecom.create_application(name='app', corp_id='corp',
            wecom_agent_id='1000002', app_secret='private-app-secret', settings={})
        self.account = await self.stores.channel.create_account(ChannelAccountCreate(
            name='application', channel_type='wecom_app', transport='webhook', agent_id=agent_id,
            session_user_id='owner', enabled=True, webhook_token='testToken', webhook_aes_key='A'*43,
            settings={'wecom_app': {'application_id': self.app_record.id}}))
        self.ctx = AppContext.for_testing(stores=self.stores, db_engine=self.engine, db_session_factory=factory)
        init_context(self.ctx)
        self.app = FastAPI()
        self.app.include_router(api_router)
        self.client = AsyncClient(transport=ASGITransport(app=self.app), base_url='http://test')
        self.path = '/api/v1/webhooks/channels/' + self.account.id
        self.crypto = WeChatCrypto('testToken', 'A'*43, 'corp')
        self.gateway = AsyncMock(return_value={'status': 'accepted', 'channel_run_id': 'trusted-run'})
        self.mock_gateway = patch('deep_agent.api.v1.webhooks.ChannelGatewayClient.receive_webhook', self.gateway)
        self.mock_gateway.start()
        self.logs = []
        self.mock_log = patch('deep_agent.api.v1.webhooks.log_event', lambda event, **fields: self.logs.append((event, fields)))
        self.mock_log.start()

    async def asyncTearDown(self):
        self.mock_gateway.stop(); self.mock_log.stop()
        await self.client.aclose()
        await self.ctx.event_persister.aclose()
        if self.ctx.wecom_profiles is not None:
            await self.ctx.wecom_profiles.close()
        reset_context()
        await self.engine.dispose()

    def message(self, kind='text', extra='<Content>private-question</Content>', **override):
        fields = {'ToUserName': 'corp', 'FromUserName': 'sender1', 'CreateTime': str(int(time.time())),
                  'MsgType': kind, 'MsgId': '123456789', 'AgentID': '1000002', **override}
        return '<xml>' + ''.join(f'<{k}>{v}</{k}>' for k,v in fields.items()) + extra + '</xml>'

    def encrypt(self, raw, *, timestamp=None, crypto=None):
        outer = (crypto or self.crypto).encrypt_message(raw, 'nonce123', str(timestamp or int(time.time())))
        data = xml_fields(outer)
        return outer, {'msg_signature': data['MsgSignature'], 'timestamp': data['TimeStamp'], 'nonce': data['Nonce']}, data['Encrypt']

    async def test_get_official_echo_without_platform_header(self):
        _, params, encrypted = self.encrypt('plain-challenge')
        response = await self.client.get(self.path, params={**params, 'echostr': encrypted})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.text, 'plain-challenge')
        self.gateway.assert_not_awaited()
        self.assertEqual(response.headers['cache-control'], 'no-store')

    async def test_post_propagates_facts_and_logs_without_content(self):
        outer, params, _ = self.encrypt(self.message())
        response = await self.client.post(self.path, params=params, content=outer)
        self.assertEqual(response.status_code, 200, response.text)
        body = self.gateway.await_args.args[1]
        self.assertEqual(body['sender_id'], 'sender1')
        self.assertEqual(body['expected_fingerprint'], account_fingerprint(self.account))
        self.assertEqual(body['request_id'], response.headers['x-request-id'])
        self.assertEqual(body['source_metadata']['wecom_app_revision'], 1)
        logs = repr(self.logs)
        for private in ('private-question', 'private-app-secret', params['msg_signature'], 'testToken'):
            self.assertNotIn(private, logs)
        self.assertIn('webhook_received', logs)
        self.assertIn('webhook_completed', logs)

    async def test_forgery_receiver_expiry_and_duplicate_parameter_rejected(self):
        outer, params, _ = self.encrypt(self.message())
        response = await self.client.post(self.path, params={**params, 'msg_signature': '0'*40}, content=outer)
        self.assertEqual(response.status_code, 403)
        for raw, expected in ((self.message(AgentID='9'),403), (self.message(ToUserName='other'),403)):
            encrypted, query, _ = self.encrypt(raw)
            self.assertEqual((await self.client.post(self.path, params=query, content=encrypted)).status_code, expected)
        encrypted, query, _ = self.encrypt(self.message(), timestamp=int(time.time())-3600)
        self.assertEqual((await self.client.post(self.path, params=query, content=encrypted)).status_code,403)
        self.assertEqual((await self.client.post(self.path, params=list(params.items())+[('nonce','second')], content=outer)).status_code,400)
        self.gateway.assert_not_awaited()

    async def test_unsafe_xml_and_oversize_rejected(self):
        _, params, _ = self.encrypt(self.message())
        for content, status in (('<!DOCTYPE xml [<!ENTITY x SYSTEM "file:///etc/passwd">]><xml>&x;</xml>',400), ('x'*(256*1024+1),413)):
            response = await self.client.post(self.path, params=params, content=content)
            self.assertEqual(response.status_code,status,response.text)
        self.gateway.assert_not_awaited()

    async def test_media_and_events_preserve_real_protocol(self):
        for kind, extra in [('image','<MediaId>image-id</MediaId>'),
                            ('voice','<MediaId>voice-id</MediaId><Format>amr</Format>'),
                            ('file','<MediaId>file-id</MediaId><FileName>../report.xlsx</FileName>')]:
            data = parse_message(self.message(kind,extra),self.account,self.app_record,request_id='request')
            self.assertEqual(data['media'][0]['kind'],kind)
            self.assertNotIn('/',data['media'][0]['filename'])
            self.assertEqual(data['source_metadata']['wecom_app_media_id'],kind+'-id')
        outer, params, _ = self.encrypt(self.message('event','<Event>enter_agent</Event>'))
        self.assertEqual((await self.client.post(self.path,params=params,content=outer)).status_code,200)
        self.gateway.assert_not_awaited()

    async def test_missing_and_other_channel_types(self):
        response = await self.client.get('/api/v1/webhooks/channels/missing')
        self.assertEqual(response.status_code,404)
        account = await self.stores.channel.create_account(ChannelAccountCreate(name='ws',bot_id='bot',secret='secret',agent_id=self.account.agent_id,session_user_id='owner'))
        response = await self.client.get('/api/v1/webhooks/channels/'+account.id)
        self.assertEqual(response.status_code,422)
        self.assertEqual(response.json()['code'],'webhook_not_supported')
        self.assertNotIn('bot',response.text)

    async def test_private_bridge_requires_auth_and_run_ownership(self):
        url = '/api/v1/internal/channels/wecom-app/send-message'
        payload = {'channel_run_id':'missing','message_type':'markdown','text':'response'}
        self.assertEqual((await self.client.post(url,json=payload)).status_code,401)
        with patch.dict('os.environ', {'DEEP_AGENT_CHANNEL_INTERNAL_TOKEN':'fixture-token'}):
            headers = {'X-Deep-Agent-Channel-Token':'fixture-token','X-Deep-Agent-Channel-Account':self.account.id}
            self.assertEqual((await self.client.post(url,headers=headers,json=payload)).status_code,409)
            self.assertEqual((await self.client.post(url,headers=headers,json={**payload,'recipient':'victim'})).status_code,422)

    async def test_nested_event_ignored_but_nested_identity_rejected(self):
        for extra, status in (('<Event>change_contact</Event><Details><Name>value</Name></Details>',200),
                              ('<Event>change_contact</Event><AgentID><bad>1000002</bad></AgentID>',400)):
            outer, params, _ = self.encrypt(self.message('event',extra))
            response = await self.client.post(self.path,params=params,content=outer)
            self.assertEqual(response.status_code,status,response.text)
        self.gateway.assert_not_awaited()

    async def test_private_bridge_trusted_sender_media_and_changed_app(self):
        resolved = await self.stores.channel.resolve_binding(account_id=self.account.id,peer_type='single',peer_id='sender1')
        facts = parse_message(self.message('file','<MediaId>file-id</MediaId><FileName>data.json</FileName>'),self.account,self.app_record,request_id='request')
        claimed = await self.stores.channel.claim_message(account_id=self.account.id,binding_id=resolved.binding.id,
            external_message_id='123456789',external_request_id='request',sender_id='sender1',session_id=resolved.session.session_id,
            payload_hash=facts['payload_hash'],source_metadata=facts['source_metadata'])
        client = SimpleNamespace(send_message=AsyncMock(return_value={'part_count':1,'message_id':'reply','status':'sent'}),
                                 media=AsyncMock(return_value=(b'{}','../data.json','application/json')))
        url = '/api/v1/internal/channels/wecom-app/'
        with patch.dict('os.environ', {'DEEP_AGENT_CHANNEL_INTERNAL_TOKEN':'fixture-token'}), patch.object(self.ctx,'get_wecom_profiles',return_value=SimpleNamespace(client=client)):
            headers = {'X-Deep-Agent-Channel-Token':'fixture-token','X-Deep-Agent-Channel-Account':self.account.id}
            response = await self.client.post(url+'send-message',headers=headers,json={'channel_run_id':claimed.run.id,'message_type':'markdown','text':'safe reply'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(client.send_message.await_args.args[1:3], ('sender1','safe reply'))
            self.assertEqual(client.send_message.await_args.kwargs['message_type'], 'markdown')
            response = await self.client.post(url+'media',headers=headers,json={'channel_run_id':claimed.run.id,'media_id':'wrong-id'})
            self.assertEqual(response.status_code,403)
            client.media.assert_not_awaited()
            response = await self.client.post(url+'media',headers=headers,json={'channel_run_id':claimed.run.id,'media_id':'file-id'})
            self.assertEqual(response.status_code,200,response.text)
            self.assertEqual(response.content,b'{}')
            self.assertNotIn('../',response.headers['content-disposition'])
            await self.stores.wecom.update_application(self.app_record.id,app_secret='rotated-fixture')
            response = await self.client.post(url+'send-message',headers=headers,json={'channel_run_id':claimed.run.id,'message_type':'markdown','text':'not sent'})
            self.assertEqual(response.status_code,409)
            self.assertEqual(client.send_message.await_count,1)

    async def test_gateway_conflict_and_timeout_never_false_ack(self):
        from deep_agent.services.channel_gateway_client import ChannelWebhookConflictError, ChannelGatewayUnavailableError
        for error, status in ((ChannelWebhookConflictError('conflict'),409),(ChannelGatewayUnavailableError('down'),503)):
            self.gateway.side_effect = error
            outer,params,_ = self.encrypt(self.message())
            response = await self.client.post(self.path,params=params,content=outer)
            self.assertEqual(response.status_code,status,response.text)
