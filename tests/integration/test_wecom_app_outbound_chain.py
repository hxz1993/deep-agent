"""Full outbound component chain with SQLite and two controlled HTTP boundaries."""
import unittest
from dataclasses import asdict
from types import SimpleNamespace as N
from unittest.mock import patch

from aiohttp import web
from deep_agent.agent.deps import AgentDeps
from deep_agent.channels.adapters.wecom_app import WecomAppAdapter
from deep_agent.channels.gateway.config import GatewayConfig
from deep_agent.channels.gateway.deepagent_client import DeepAgentChannelClient
from deep_agent.channels.message_tool import MessageComponent, prepare_components
from deep_agent.channels.operations import ChannelOperationService, MessageOperationRequest
from deep_agent.schemas.attachment import AttachmentRef
from deep_agent.services.wecom_client import WecomClient
from deep_agent.services.wecom_webhook import parse_message
from tests.integration import test_wecom_webhook_api as webhook_fixtures


class WecomOutboundChainTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = webhook_fixtures.WebhookApiTests.asyncSetUp
    asyncTearDown = webhook_fixtures.WebhookApiTests.asyncTearDown
    message = webhook_fixtures.WebhookApiTests.message

    async def test_file_and_card_reach_enterprise_with_trusted_identity(self):
        record = self.app_record
        account = self.account
        store = self.stores.channel
        resolved = await store.resolve_binding(account_id=account.id, peer_type='single', peer_id='sender1')
        facts = parse_message(self.message(), account, record, request_id='request')
        claim = await store.claim_message(account_id=account.id, binding_id=resolved.binding.id,
            external_message_id='123456789', external_request_id='request', sender_id='sender1',
            session_id=resolved.session.session_id, payload_hash=facts['payload_hash'], source_metadata=facts['source_metadata'])
        run = await store.update_run(claim.run.id, agent_run_id='agent-run', processing_status='running')
        uploads, messages, bridge_calls = [], [], []

        async def token(request):
            self.assertEqual(request.query['corpid'], record.corp_id)
            return web.json_response({'errcode': 0, 'access_token': 'controlled-token', 'expires_in': 7200})

        async def upload(request):
            self.assertEqual(request.query['access_token'], 'controlled-token')
            field = await (await request.multipart()).next()
            uploads.append((request.query['type'], field.filename, bytes(await field.read())))
            return web.json_response({'errcode': 0, 'media_id': 'controlled-media'})

        async def send(request):
            messages.append(await request.json())
            return web.json_response({'errcode': 0, 'msgid': f'result-{len(messages)}'})

        async def bridge(request):
            body = await request.read()
            import json
            bridge_calls.append(json.loads(body))
            response = await self.client.request(request.method, request.path, headers=dict(request.headers), content=body)
            return web.Response(status=response.status_code, body=response.content, content_type='application/json')

        external = web.Application()
        external.router.add_get('/gettoken', token)
        external.router.add_post('/media/upload', upload)
        external.router.add_post('/message/send', send)
        internal = web.Application()
        internal.router.add_post('/api/v1/internal/channels/wecom-app/send-component', bridge)
        runners = []
        enterprise = WecomClient()
        gateway = None
        adapter = None
        try:
            addresses = []
            for server in (external, internal):
                runner = web.AppRunner(server, access_log=None)
                runners.append(runner)
                await runner.setup()
                site = web.TCPSite(runner, '127.0.0.1', 0)
                await site.start()
                addresses.append(f'http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}')
            enterprise.BASE_URL = addresses[0]
            gateway = DeepAgentChannelClient(GatewayConfig(addresses[1], 'fixture-token', 1, .1, 10, '127.0.0.1', 0, '', ''))
            adapter = WecomAppAdapter(account)
            adapter.client = gateway
            await adapter.start(None)
            adapter.bind_run(N(message_id='123456789'), run.id)
            job = N(stopped=False, terminal=False, run_id=run.id, outbound_tasks=set(), sent_text_hashes=set(), message_tool_sent=False)
            managed = N(stopping=False, adapter=adapter, fingerprint='fixture')
            runtime = N(store=store, _accounts={account.id: managed}, registry=N(fingerprint=lambda _: 'fixture'),
                        service=N(peers={(account.id, 'single', 'sender1'): N(active=job)}))
            operations = ChannelOperationService(runtime)

            class AuthorizedFixtureFiles:
                async def download_bounded(inner_self, file_id, headers, *, max_bytes):
                    self.assertEqual(file_id, 'report-file')
                    self.assertGreaterEqual(max_bytes, 13)
                    return b'report-bytes'

            deps = AgentDeps(session_id=resolved.session.session_id, agent_id=account.agent_id, user='owner', run_id='agent-run')
            deps.attachment_by_id = {'report-file': AttachmentRef(id='report-file', name='report.txt')}
            deps.file_store = AuthorizedFixtureFiles()
            payload = {'title': '报告', 'description': '已完成', 'url': 'https://example.test/report', 'btntxt': '查看'}
            items = await prepare_components(deps, [MessageComponent(type='file', file_id='report-file'),
                MessageComponent(type='textcard', payload=payload)], adapter.message_capabilities())
            command = MessageOperationRequest(account_id=account.id, channel_run_id=run.id,
                session_id=resolved.session.session_id, agent_id=account.agent_id, user_id='owner', agent_run_id='agent-run',
                components=[{k: v for k, v in asdict(item).items() if k != 'data'} for item in items])
            with patch.dict('os.environ', {'DEEP_AGENT_CHANNEL_INTERNAL_TOKEN': 'fixture-token'}), \
                    patch.object(self.ctx, 'get_wecom_profiles', return_value=N(client=enterprise)):
                result = await operations.send(command, items)
            self.assertEqual(result['status'], 'completed', result)
            self.assertEqual(uploads, [('file', 'report.txt', b'report-bytes')])
            self.assertEqual([item['msgtype'] for item in messages], ['file', 'textcard'])
            self.assertTrue(all(item['touser'] == 'sender1' and item['agentid'] == 1000002 for item in messages))
            self.assertEqual(messages[0]['file'], {'media_id': 'controlled-media'})
            self.assertEqual(messages[1]['textcard'], payload)
            self.assertTrue(all(item['channel_run_id'] == run.id for item in bridge_calls))
            self.assertEqual(bridge_calls[1]['payload'], payload)
            self.assertNotIn('data_base64', bridge_calls[1])
        finally:
            if adapter is not None:
                await adapter.stop()
            if gateway is not None:
                await gateway.close()
            await enterprise.close()
            for runner in reversed(runners):
                await runner.cleanup()
