import asyncio
from unittest.mock import patch

import httpx

from tests.integration.test_channel_accounts_api import ChannelAccountsApiTests
from deep_agent.services.wecom_registration import WecomRegistrationService


class WecomRegistrationApiTests(ChannelAccountsApiTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        def handler(request):
            data = ({'scode': 'private-poll-code', 'auth_url': 'https://work.weixin.qq.com/ai/qc/c?s=fixture'}
                    if request.url.path.endswith('generate') else
                    {'status': 'success', 'bot_info': {'botid': 'qr-bot', 'secret': 'private-wecom-secret'}})
            return httpx.Response(200, json={'data': data})
        self.service = WecomRegistrationService(transport=httpx.MockTransport(handler), poll_interval=.001)
        self.patches = [patch('deep_agent.services.wecom_registration.registration_service', self.service),
                        patch('deep_agent.api.v1.wecom_registration.registration_service', self.service)]
        for item in self.patches:
            item.start()
        self.root = '/api/v1/channel-accounts/wecom/registrations'

    async def asyncTearDown(self):
        await self.service.aclose()
        for item in self.patches:
            item.stop()
        await super().asyncTearDown()

    async def authorize(self):
        response = await self.request('POST', self.root, json={})
        self.assertEqual(response.status_code, 201, response.text)
        key = response.json()['registration_id']
        await asyncio.sleep(0)
        return key

    def scan_payload(self, key):
        payload = self.payload()
        del payload['bot_id'], payload['secret']
        return {**payload, 'channel_type': 'wecom', 'registration_id': key}

    async def test_qr_save_owner_platform_isolation_private_credentials_and_retry(self):
        key = await self.authorize()
        status = await self.request('GET', self.root + '/' + key)
        self.assertEqual(status.json()['status'], 'authorized')
        self.assertNotIn('private-', status.text)
        self.assertEqual((await self.request('GET', self.root + '/' + key, user='other')).status_code, 404)
        payload = self.scan_payload(key)
        self.assertEqual((await self.request('POST', '/api/v1/channel-accounts', json={**payload, 'channel_type': 'feishu'})).status_code, 404)
        existing = await self.request('POST', '/api/v1/channel-accounts', json={**self.payload(), 'bot_id': 'qr-bot'})
        self.assertEqual(existing.status_code, 201, existing.text)
        self.assertEqual((await self.request('POST', '/api/v1/channel-accounts', json=payload)).status_code, 409)
        self.assertEqual(self.service.snapshot(key, 'owner')['status'], 'authorized')
        await self.request('DELETE', '/api/v1/channel-accounts/' + existing.json()['id'])
        saved = await self.request('POST', '/api/v1/channel-accounts', json=payload)
        self.assertEqual(saved.status_code, 201, saved.text)
        self.assertNotIn('private-wecom-secret', saved.text)
        row = await self.stores.channel.get_account(saved.json()['id'])
        self.assertEqual((row.bot_id, row.secret), ('qr-bot', 'private-wecom-secret'))
        self.assertEqual((await self.request('POST', '/api/v1/channel-accounts', json=payload)).status_code, 409)

    async def test_cancel_creates_no_account_and_does_not_accept_remote_name(self):
        self.assertEqual((await self.request('POST', self.root, json={'app_name': 'not supported'})).status_code, 422)
        key = await self.authorize()
        self.assertEqual((await self.request('DELETE', self.root + '/' + key, user='other')).status_code, 404)
        self.assertEqual((await self.request('DELETE', self.root + '/' + key)).status_code, 204)
        self.assertEqual(await self.stores.channel.list_accounts(), [])
        self.assertEqual((await self.request('POST', '/api/v1/channel-accounts', json=self.scan_payload(key))).status_code, 409)

    async def test_admin_required_for_all_actions(self):
        from deep_agent.core.context import get_context
        from deep_agent.security import ConsoleAdminAuthorizer
        from unittest.mock import AsyncMock
        key = await self.authorize()
        self.app.dependency_overrides.clear()
        get_context().console_admin_authorizer = ConsoleAdminAuthorizer(AsyncMock(return_value=False))
        for method, path in [('POST', self.root), ('GET', self.root + '/' + key), ('DELETE', self.root + '/' + key)]:
            denied = await self.request(method, path, user='ordinary-user')
            self.assertIn(denied.status_code, (401, 403, 503))
            self.assertNotIn('verification_url', denied.text)
