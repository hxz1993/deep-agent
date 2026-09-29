"""Encrypted public HTTP -> Gateway HTTP -> real Agent SSE -> private send bridge.

Only the external WeCom sender and model are synthetic. File-backed SQLite and
both internal HTTP boundaries are real; no shared databases or fixed ports.
"""
from __future__ import annotations
import asyncio
import os
import time
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from aiohttp import web
from aiohttp.test_utils import TestServer
from pydantic_ai.models.function import FunctionModel
from wechatpy.enterprise.crypto import WeChatCrypto

from deep_agent.channels.gateway.app import create_gateway_app
from deep_agent.channels.gateway.config import GatewayConfig
from deep_agent.channels.gateway.runtime import ChannelRuntime
from deep_agent.schemas.channel import ChannelAccountCreate
from deep_agent.services.wecom_webhook import xml_fields
from tests.helpers.request_identity import TEST_USER_ID
from tests.integration.helpers.chat import mock_request_disconnect
from tests.integration import test_channel_message_persistence as facts_fixture


class WebhookPersistenceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = facts_fixture.ChannelMessagePersistenceIntegrationTests.asyncSetUp
    _history = facts_fixture.ChannelMessagePersistenceIntegrationTests._history
    _run_record = facts_fixture.ChannelMessagePersistenceIntegrationTests._run_record
    _drain = facts_fixture.ChannelMessagePersistenceIntegrationTests._drain

    async def _http_server(self, asgi_app):
        async def forward(request):
            body = await request.read()
            response = web.StreamResponse()
            self.bridge_bodies = getattr(self, 'bridge_bodies', [])
            received = False
            done = asyncio.Event()
            async def receive():
                nonlocal received
                if not received:
                    received = True
                    return {'type':'http.request', 'body':body, 'more_body':False}
                await done.wait()
                return {'type':'http.disconnect'}
            async def send(message):
                if message['type'] == 'http.response.start':
                    response.set_status(message['status'])
                    for key,value in message.get('headers',[]):
                        response.headers.add(key.decode('latin1'),value.decode('latin1'))
                    await response.prepare(request)
                elif message['type'] == 'http.response.body':
                    self.bridge_bodies.append((request.path, message.get('body',b'')))
                    await response.write(message.get('body',b''))
                    if not message.get('more_body',False):
                        await response.write_eof(); done.set()
            scope = {'type':'http','asgi':{'version':'3.0'},'http_version':'1.1',
                     'method':request.method,'scheme':'http','path':request.path,
                     'raw_path':request.path.encode(),'query_string':request.query_string.encode(),
                     'root_path':'','headers':[(k.lower(),v) for k,v in request.raw_headers],
                     'client':('127.0.0.1',12345),'server':('test',80)}
            try:
                await asgi_app(scope,receive,send)
            finally:
                done.set()
            return response
        app=web.Application(); app.router.add_route('*','/{path:.*}',forward)
        server=TestServer(app); await server.start_server()
        self.addAsyncCleanup(server.close)
        return str(server.make_url('/')).rstrip('/')

    async def test_encrypted_message_is_persisted_before_send_and_retry_is_deduplicated(self):
        application=await self.ctx.stores.wecom.create_application(name='synthetic app',
            corp_id='corp',wecom_agent_id='1000002',app_secret='fixture-secret',settings={})
        account=await self.ctx.stores.channel.create_account(ChannelAccountCreate(
            name='synthetic webhook',channel_type='wecom_app',transport='webhook',
            agent_id=self._agent_id,session_user_id=TEST_USER_ID,enabled=True,
            webhook_token='testToken',webhook_aes_key='A'*43,
            settings={'wecom_app':{'application_id':application.id}}))
        api_url=await self._http_server(self._app)
        config=GatewayConfig(api_url=api_url,internal_token='fixture-token',poll_interval=30,
            stream_interval=0,test_timeout=2,host='127.0.0.1',port=0,
            platform_authorization='',platform_cookie='')
        runtime=ChannelRuntime(self.ctx.stores.channel,config)
        self.addAsyncCleanup(runtime.stop)
        gateway=create_gateway_app(); gateway.state.gateway=SimpleNamespace(config=config,runtime=runtime)
        gateway_url=await self._http_server(gateway)
        env=patch.dict(os.environ,{'DEEP_AGENT_CHANNEL_GATEWAY_URL':gateway_url}); env.start();self.addCleanup(env.stop)
        deliveries=[]
        async def delivered(app,recipient,text, *, before_send=None):
            if before_send: await before_send()
            run=await self._run_record(account.id,'900001')
            history=await self._history(run.session_id)
            self.assertEqual(sum(i['kind']=='user_message' for i in history['items']),1)
            self.assertTrue(any(i['kind']=='assistant_message' and i['text']=='WEBHOOK_FINAL' for i in history['items']))
            deliveries.append((app.id,recipient,text))
            return {'part_count':1,'message_id':'fixture-out'}
        client=self.ctx.get_wecom_profiles().client
        token=patch.object(client,'token',AsyncMock(return_value='fixture-token')); token.start();self.addCleanup(token.stop)
        sender=patch.object(client,'send_text',AsyncMock(side_effect=delivered)); sender.start();self.addCleanup(sender.stop)
        model_calls=[]
        async def answer(messages,info):
            model_calls.append(messages)
            yield 'WEBHOOK_';yield 'FINAL'
        timestamp=str(int(time.time()))
        plain=f'<xml><ToUserName>corp</ToUserName><FromUserName>member-one</FromUserName><CreateTime>{timestamp}</CreateTime><MsgType>text</MsgType><Content>synthetic webhook question</Content><MsgId>900001</MsgId><AgentID>1000002</AgentID></xml>'
        encrypted=WeChatCrypto('testToken','A'*43,'corp').encrypt_message(plain,'nonce',timestamp)
        envelope=xml_fields(encrypted)
        params={'msg_signature':envelope['MsgSignature'],'timestamp':envelope['TimeStamp'],'nonce':envelope['Nonce']}
        path=f'/api/v1/webhooks/channels/{account.id}'
        with patch('deep_agent.agent.factory._resolve_model',return_value=FunctionModel(stream_function=answer)),mock_request_disconnect():
            await runtime.start()
            response=await self.http.post(path,params=params,content=encrypted)
            self.assertEqual(response.status_code,200,response.text)
            await self._drain(runtime.service)
            again=await self.http.post(path,params=params,content=encrypted)
            self.assertEqual(again.status_code,200,again.text)
            await self._drain(runtime.service)
        run=await self._run_record(account.id,'900001')
        self.assertEqual(run.processing_status,'completed', repr(self.bridge_bodies))
        self.assertEqual(run.delivery_status,'sent')
        self.assertEqual(run.sender_id,'member-one')
        self.assertIsNotNone(run.agent_run_id)
        session=await self.ctx.stores.channel.get_channel_session(account.id,run.session_id)
        self.assertEqual(session.source_type,'channel:wecom_app')
        self.assertEqual(session.source_id,account.id)
        events=await self.ctx.stores.coordinator.list_all_events(run.session_id)
        self.assertEqual(sum(event.event_type=='user/message' for event in events),1)
        self.assertEqual(len(model_calls),1)
        self.assertEqual(deliveries,[(application.id,'member-one','WEBHOOK_FINAL')])
