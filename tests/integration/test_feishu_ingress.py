"""Official Feishu wire -> isolated gateway -> HTTP/SSE -> model -> durable history."""
from __future__ import annotations

import asyncio
import json
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import patch

from lark_channel.ws.pb.pbbp2_pb2 import Frame
from pydantic_ai.models.function import FunctionModel
from websockets.asyncio.server import serve

from deep_agent.channels.adapters.feishu import FeishuAdapter
from deep_agent.schemas.channel import ChannelAccountCreate
from tests.helpers.request_identity import TEST_USER_ID
from tests.integration.helpers.chat import mock_request_disconnect
from tests.integration import test_channel_message_persistence as persistence_fixture
from tests.unit.feishu_adapter_fixtures import local_sdk_worker


class FeishuIngressIntegrationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = persistence_fixture.ChannelMessagePersistenceIntegrationTests.asyncSetUp
    _create_ingress = persistence_fixture.ChannelMessagePersistenceIntegrationTests._create_ingress
    _history = persistence_fixture.ChannelMessagePersistenceIntegrationTests._history
    _run_record = persistence_fixture.ChannelMessagePersistenceIntegrationTests._run_record
    _drain = persistence_fixture.ChannelMessagePersistenceIntegrationTests._drain

    async def test_real_sdk_group_and_private_roundtrips_keep_durable_sources(self):
        # Reuse only the existing bridge/client setup; the fixture WeCom adapter
        # is stopped and receives no events. Feishu goes through its real worker.
        _, service, old_adapter, _, requests = await self._create_ingress(stream_interval=0)
        await old_adapter.stop()
        account = await self.ctx.stores.channel.create_account(ChannelAccountCreate(
            name='isolated Feishu', channel_type='feishu', bot_id='cli_ingress', secret='fixture',
            agent_id=self._agent_id, session_user_id=TEST_USER_ID, enabled=True,
        ))
        outbound = []
        final_histories = []
        main_loop = asyncio.get_running_loop()
        async def final_history():
            return await self._history(requests[-1]["session_id"])

        peers = []
        ready = asyncio.Event()
        admitted = []
        async def ws_handler(connection):
            peers.append(connection)
            ready.set()
            async for raw in connection:
                pass  # The official SDK's binary ACK/ping is not a business receipt.

        async with serve(ws_handler, '127.0.0.1', 0) as ws_server:
            ws_port = ws_server.sockets[0].getsockname()[1]
            class Handler(BaseHTTPRequestHandler):
                def log_message(self, *_): pass
                def handle_request(self):
                    content = self.rfile.read(int(self.headers.get('Content-Length', 0)))
                    payload = json.loads(content) if content else {}
                    if self.path == '/callback/ws/endpoint':
                        data = {'code': 0, 'data': {'URL': f'ws://127.0.0.1:{ws_port}/?device_id=fixture&service_id=1',
                                'ClientConfig': {'PingInterval': 30, 'ReconnectInterval': 1, 'ReconnectNonce': 0, 'ReconnectCount': 0}}}
                    elif '/auth/' in self.path:
                        data = {'code': 0, 'tenant_access_token': 'local-fixture', 'expire': 7200}
                    elif '/bot/' in self.path:
                        data = {'code': 0, 'bot': {'open_id': 'ou_bot', 'app_name': 'Fixture'}}
                    else:
                        outbound.append((self.command, self.path, payload))
                        data = {'code': 0, 'data': {'card_id': f'card-{len(outbound)}', 'message_id': f'reply-{len(outbound)}'}}
                    if self.path.endswith('/settings') and 'false' in str(payload.get('settings', '')).lower():
                        # Verify persistence before acknowledging the SDK's final
                        # card update, not merely after the whole run settles.
                        future = asyncio.run_coroutine_threadsafe(final_history(), main_loop)
                        final_histories.append(future.result(timeout=5))
                    raw = json.dumps(data).encode()
                    self.send_response(200); self.send_header('Content-Type', 'application/json')
                    self.send_header('Content-Length', str(len(raw))); self.end_headers(); self.wfile.write(raw)
                do_POST = do_GET = do_PATCH = do_PUT = handle_request
            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            adapter = FeishuAdapter(SimpleNamespace(id=account.id, bot_id=account.bot_id, secret=account.secret,
                settings={'_test_domain': f'http://127.0.0.1:{server.server_port}'}),
                infinite_retry=False, _worker_target=local_sdk_worker)
            async def receive(message):
                admitted.append(message)
                await service.receive(account, adapter, message)
            model_calls = []
            async def answer(messages, info):
                model_calls.append(messages)
                yield '冻结业务结果：返利金额 120 元。'
            async def send(mid, sender, chat, group):
                frame = Frame(SeqID=len(admitted) + 1, LogID=1, service=1, method=1)
                for key, value in {'type': 'event', 'message_id': f'wire-{mid}-{time.monotonic()}', 'sum': '1', 'seq': '0', 'trace_id': 'fixture'}.items():
                    header = frame.headers.add(); header.key = key; header.value = value
                message = {'message_id': mid, 'chat_id': chat, 'chat_type': 'group' if group else 'p2p',
                           'message_type': 'text', 'content': json.dumps({'text': '@_user_1 请核算返利' if group else '请核算返利'}),
                           'create_time': str(int(time.time() * 1000))}
                if group:
                    message['mentions'] = [{'key': '@_user_1', 'id': {'open_id': 'ou_bot'}, 'name': 'Fixture', 'tenant_key': 'tenant_fixture'}]
                frame.payload = json.dumps({'schema': '2.0', 'header': {'event_id': f'event-{mid}',
                    'event_type': 'im.message.receive_v1', 'tenant_key': 'tenant_fixture', 'app_id': account.bot_id,
                    'create_time': str(int(time.time() * 1000))}, 'event': {
                    'sender': {'sender_id': {'open_id': sender}, 'sender_type': 'user', 'tenant_key': 'tenant_fixture'},
                    'message': message}}).encode()
                await peers[0].send(frame.SerializeToString())
            try:
                await adapter.start(receive)
                await asyncio.wait_for(ready.wait(), 10)
                with patch('deep_agent.agent.factory._resolve_model', return_value=FunctionModel(stream_function=answer)), mock_request_disconnect():
                    specimens = [('om_g1', 'ou_one', 'oc_group', True), ('om_g2', 'ou_two', 'oc_group', True),
                                 ('om_p1', 'ou_one', 'oc_private_one', False), ('om_p2', 'ou_two', 'oc_private_two', False)]
                    for number, specimen in enumerate(specimens, start=1):
                        await send(*specimen)
                        async with asyncio.timeout(15):
                            while len(requests) < number:
                                await asyncio.sleep(.02)
                        await self._drain(service)
                    count = len(model_calls)
                    await send(*specimens[0])
                    await asyncio.sleep(.3)
                    await self._drain(service)
                    self.assertEqual(len(model_calls), count)
                self.assertEqual(len(model_calls), 4)
                session_ids = [request['session_id'] for request in requests]
                self.assertEqual(session_ids[0], session_ids[1])
                self.assertEqual(len(set(session_ids)), 3)
                for mid, sender, chat, group in specimens:
                    record = await self._run_record(account.id, mid)
                    self.assertEqual(record.sender_id, sender)
                    self.assertEqual(record.tenant_id, 'tenant_fixture')
                    self.assertEqual(record.processing_status, 'completed')
                    self.assertEqual(record.delivery_status, 'sent', record.error_code)
                    history = await self._history(record.session_id)
                    self.assertIn('返利金额 120 元', str(history))
                for request in requests:
                    payload = next(ref['payload'] for ref in request['context_refs'] if ref['type'] == 'channel.message_source')
                    self.assertEqual(payload['channel_type'], 'feishu')
                    self.assertEqual(payload['tenant_id'], 'tenant_fixture')
                self.assertTrue(any('/cardkit/' in path for _, path, _ in outbound), outbound)
                self.assertTrue(any('/im/v1/messages/' in path for _, path, _ in outbound), outbound)
                self.assertIn('返利金额 120 元', str(outbound))
                self.assertEqual(len(final_histories), 4)
                self.assertTrue(all('返利金额 120 元' in str(history) for history in final_histories))
            finally:
                await service.close_account(account.id)
                await adapter.stop()
                await asyncio.to_thread(server.shutdown)
                server.server_close(); thread.join(timeout=2)
