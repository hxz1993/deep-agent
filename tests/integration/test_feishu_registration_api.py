import asyncio
from unittest.mock import patch

from tests.integration.test_channel_accounts_api import ChannelAccountsApiTests
from deep_agent.services.feishu_registration import FeishuRegistrationService


class FeishuRegistrationApiTests(ChannelAccountsApiTests):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        async def register(**kwargs):
            kwargs['on_qr_code']({'url': 'https://open.feishu.cn/page/cli?user_code=fixture', 'expire_in': 60})
            return {'client_id': 'cli_fixture', 'client_secret': 'private-feishu-secret'}
        self.service = FeishuRegistrationService(register=register)
        self.patches = [patch('deep_agent.services.feishu_registration.registration_service', self.service),
                        patch('deep_agent.api.v1.feishu_registration.registration_service', self.service)]
        for p in self.patches:
            p.start()

    async def asyncTearDown(self):
        await self.service.aclose()
        for p in self.patches:
            p.stop()
        await super().asyncTearDown()

    async def test_registration_save_ownership_and_no_secret_exposure(self):
        root = '/api/v1/channel-accounts/feishu/registrations'
        response = await self.request('POST', root, json={})
        self.assertEqual(response.status_code, 201, response.text)
        key = response.json()['registration_id']
        await asyncio.sleep(0)
        self.assertEqual((await self.request('GET', root + '/' + key, user='other')).status_code, 404)
        status = await self.request('GET', root + '/' + key)
        self.assertEqual(status.json()['status'], 'authorized')
        self.assertNotIn('private-feishu-secret', status.text)
        payload = self.payload()
        del payload['bot_id'], payload['secret']
        payload.update(channel_type='feishu', registration_id=key)
        response = await self.request('POST', '/api/v1/channel-accounts', json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()['bot_id'], 'cli_fixture')
        self.assertNotIn('private-feishu-secret', response.text)
        self.assertEqual((await self.stores.channel.get_account(response.json()['id'])).secret, 'private-feishu-secret')
        self.assertEqual((await self.request('POST', '/api/v1/channel-accounts', json=payload)).status_code, 409)

    async def test_registration_app_name_validation_and_snapshot(self):
        root = '/api/v1/channel-accounts/feishu/registrations'
        for name in ['x' * 192, 42, None]:
            response = await self.request('POST', root, json={'app_name': name})
            self.assertEqual(response.status_code, 422, response.text)
        self.assertFalse(self.service.entries)
        response = await self.request('POST', root, json={'app_name': '  财务助手  '})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.service.entries[response.json()['registration_id']].app_name, '财务助手')
        response = await self.request('POST', root, json={'app_name': '  '})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(self.service.entries[response.json()['registration_id']].app_name, 'DeepAgent')

    async def test_cancel_does_not_create_account_and_manual_create(self):
        root = '/api/v1/channel-accounts/feishu/registrations'
        response = await self.request('POST', root, json={})
        key = response.json()['registration_id']
        self.assertEqual((await self.request('DELETE', root + '/' + key)).status_code, 204)
        self.assertEqual(await self.stores.channel.list_accounts(), [])
        response = await self.request('POST', '/api/v1/channel-accounts', json={**self.payload(), 'channel_type': 'feishu'})
        self.assertEqual(response.status_code, 201, response.text)
        response = await self.request('PUT', '/api/v1/channel-accounts/' + response.json()['id'], json={'secret': ''})
        self.assertTrue(response.json()['secret_set'])

    async def test_admin_required_for_every_registration_action(self):
        from deep_agent.core.context import get_context
        from deep_agent.security import ConsoleAdminAuthorizer
        from unittest.mock import AsyncMock
        root = '/api/v1/channel-accounts/feishu/registrations'
        response = await self.request('POST', root, json={})
        key = response.json()['registration_id']
        self.app.dependency_overrides.clear()
        get_context().console_admin_authorizer = ConsoleAdminAuthorizer(AsyncMock(return_value=False))
        for method, path in [('POST', root), ('GET', root + '/' + key), ('DELETE', root + '/' + key)]:
            denied = await self.request(method, path, user='ordinary-user')
            self.assertIn(denied.status_code, (401, 403, 503))
            self.assertNotIn('verification_url', denied.text)

    async def test_rejects_registration_with_manual_credentials_or_wrong_channel(self):
        for payload in ({**self.payload(), 'registration_id': 'x'},
                        {**self.payload(), 'channel_type': 'feishu', 'registration_id': 'x'},
                        {**self.payload(), 'channel_type': 'unsupported'}):
            result = await self.request('POST', '/api/v1/channel-accounts', json=payload)
            self.assertEqual(result.status_code, 422)

    async def test_provider_settings_are_isolated_and_validate_updates(self):
        for settings in ({'feishu': {'domain': 'https://attacker.invalid'}},
                         {'feishu': {'secret': 'hidden-secret'}},
                         {'feishu': {'require_mention': False}},
                         {'feishu': {'group_allowlist': ['bad\nheader']}},
                         {'wecom': {}}):
            response = await self.request('POST', '/api/v1/channel-accounts', json={
                **self.payload(), 'channel_type': 'feishu', 'settings': settings})
            self.assertEqual(response.status_code, 422, response.text)
        response = await self.request('POST', '/api/v1/channel-accounts', json={
            **self.payload(), 'channel_type': 'feishu', 'settings': {'feishu': {'group_allowlist': ['oc_group']}}})
        self.assertEqual(response.status_code, 201, response.text)
        path = '/api/v1/channel-accounts/' + response.json()['id']
        self.assertEqual((await self.request('PUT', path, json={'settings': {'wecom': {}}})).status_code, 409)
        response = await self.request('POST', '/api/v1/channel-accounts', json=self.payload())
        path = '/api/v1/channel-accounts/' + response.json()['id']
        self.assertEqual((await self.request('PUT', path, json={'settings': {'feishu': {}}})).status_code, 409)

    async def test_registration_limits_and_unknown_cancel(self):
        root = '/api/v1/channel-accounts/feishu/registrations'
        self.service.capacity = 1
        first = await self.request('POST', root, json={})
        self.assertEqual(first.status_code, 201)
        self.assertEqual((await self.request('POST', root, json={})).status_code, 429)
        self.assertEqual((await self.request('DELETE', root + '/unknown')).status_code, 404)
        self.assertEqual((await self.request('DELETE', root + '/' + first.json()['registration_id'], user='other')).status_code, 404)

    async def test_store_refuses_unresolved_registration(self):
        from deep_agent.schemas.channel import ChannelAccountCreate
        payload = self.payload()
        del payload['bot_id'], payload['secret']
        data = ChannelAccountCreate(**payload, channel_type='feishu', registration_id='unresolved')
        with self.assertRaisesRegex(ValueError, '渠道凭据'):
            await self.stores.channel.create_account(data)
        self.assertEqual(await self.stores.channel.list_accounts(), [])

    async def test_failed_channel_save_keeps_registration_available_for_retry(self):
        root = '/api/v1/channel-accounts/feishu/registrations'
        existing = await self.request('POST', '/api/v1/channel-accounts', json={
            **self.payload(), 'channel_type': 'feishu', 'bot_id': 'cli_fixture'})
        self.assertEqual(existing.status_code, 201, existing.text)
        response = await self.request('POST', root, json={})
        key = response.json()['registration_id']
        await asyncio.sleep(0)
        payload = self.payload()
        del payload['bot_id'], payload['secret']
        payload.update(channel_type='feishu', registration_id=key)
        failed = await self.request('POST', '/api/v1/channel-accounts', json=payload)
        self.assertEqual(failed.status_code, 409, failed.text)
        self.assertEqual(self.service.snapshot(key, 'owner')['status'], 'authorized')
        removed = await self.request('DELETE', '/api/v1/channel-accounts/' + existing.json()['id'])
        self.assertEqual(removed.status_code, 200, removed.text)
        saved = await self.request('POST', '/api/v1/channel-accounts', json=payload)
        self.assertEqual(saved.status_code, 201, saved.text)
        self.assertEqual(self.service.snapshot(key, 'owner')['status'], 'consumed')
