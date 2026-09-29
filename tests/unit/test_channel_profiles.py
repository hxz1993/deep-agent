from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import AsyncMock

from deep_agent.services.channel_profiles import ChannelProfileService


def account(channel='feishu', enabled=True):
    settings = ({'feishu': {'user_details_enabled': enabled}} if channel == 'feishu' else
                {'wecom': {'user_details_enabled': enabled, 'application_id': 'app'}})
    return SimpleNamespace(id='account', enabled=True, channel_type=channel, bot_id='bot', secret='test', settings=settings)


class ChannelProfilesTests(IsolatedAsyncioTestCase):
    async def test_api_rechecks_name_switch_for_both_gateway_profile_channels(self):
        from deep_agent.api.v1.chat import _current_channel_context

        for channel in ('feishu', 'dingtalk'):
            for enabled in (False, True):
                with self.subTest(channel=channel, enabled=enabled):
                    current = account(channel)
                    current.agent_id = 'agent'
                    current.session_user_id = 'owner'
                    current.settings = {channel: {'user_details_enabled': enabled}}
                    record = SimpleNamespace(sender_id='A', sender_name='trusted-name',
                                             tenant_id='tenant', thread_id='thread')
                    store = SimpleNamespace(get_account=AsyncMock(return_value=current),
                                            get_message=AsyncMock(return_value=record))
                    ctx = SimpleNamespace(stores=SimpleNamespace(channel=store))
                    payload = {'sender_id': 'A', 'message_id': 'message', 'name': 'untrusted'}
                    body = SimpleNamespace(context_refs=[SimpleNamespace(
                        type='channel.message_source', payload=payload)])
                    await _current_channel_context(ctx, body, current)
                    self.assertEqual(payload.get('name'), 'trusted-name' if enabled else None)
                    self.assertEqual(payload['tenant_id'], 'tenant')
                    self.assertEqual(record.sender_name, 'trusted-name')

    async def test_wecom_context_uses_only_fresh_persisted_names_for_both_channels(self):
        from datetime import datetime, timedelta, timezone
        from deep_agent.api.v1.chat import _current_channel_context
        for channel in ('wecom', 'wecom_app'):
            for days in (-1, 1):
                with self.subTest(channel=channel, days=days):
                    current = account(channel)
                    current.agent_id, current.session_user_id = 'agent', 'owner'
                    current.settings = {channel: {'application_id': 'app', 'user_details_enabled': True}}
                    profile = SimpleNamespace(userid='member', name='缓存姓名',
                        expires_at=datetime.now(timezone.utc) + timedelta(days=days))
                    lookup = AsyncMock(return_value=profile)
                    ctx = SimpleNamespace(stores=SimpleNamespace(
                        channel=SimpleNamespace(get_account=AsyncMock(return_value=current)),
                        wecom=SimpleNamespace(find_profile=lookup)))
                    payload = {'sender_id': 'raw', 'name': '旧姓名'}
                    body = SimpleNamespace(context_refs=[SimpleNamespace(type='channel.message_source', payload=payload)])
                    await _current_channel_context(ctx, body, current)
                    lookup.assert_awaited_once_with('app', 'raw')
                    self.assertEqual(payload['name'], '缓存姓名' if days > 0 else None)
                    self.assertEqual(payload['userid'], 'member')

    def service(self, row):
        store = SimpleNamespace(get_account=AsyncMock(return_value=row))
        self.provider = SimpleNamespace(resolve=AsyncMock(return_value={
            'userid': 'member', 'name': '姓名', 'status': 'resolved', 'application_id': 'app', 'application_revision': 1}))
        ctx = SimpleNamespace(stores=SimpleNamespace(channel=store), get_wecom_profiles=lambda: self.provider)
        self.gateway = SimpleNamespace(resolve_users=AsyncMock(return_value={'items': [
            {'sender_id': 'A', 'userid': None, 'name': '姓名', 'status': 'resolved'}]}))
        self.store = store
        return ChannelProfileService(ctx, gateway=self.gateway)

    async def test_wecom_app_uses_persisted_provider_not_gateway_cache(self):
        row = account('wecom_app')
        row.settings = {'wecom_app': {'application_id': 'app'}}
        service = self.service(row)
        result = await service.resolve_many(row, ['A'])
        self.assertEqual(result['items']['A']['name'], '姓名')
        self.provider.resolve.assert_awaited_once_with(row, 'A', await_refresh=True)
        self.gateway.resolve_users.assert_not_called()

    async def test_dingtalk_passive_profiles_without_feishu_switch_or_wecom_app(self):
        row = account('dingtalk')
        row.settings = {'dingtalk': {'user_details_enabled': True}}
        service = self.service(row)
        result = await service.resolve_many(SimpleNamespace(id='account'), ['A', 'B'])
        self.assertTrue(result['user_details_enabled'])
        self.assertEqual(result['items']['A']['name'], '姓名')
        self.assertFalse(result['profiles_complete'])
        self.provider.resolve.assert_not_called()
        self.gateway.resolve_users.assert_awaited_once()

    async def test_disabled_does_not_query_and_does_not_reuse_snapshot(self):
        service = self.service(account(enabled=False))
        result = await service.resolve_many(SimpleNamespace(id='account'), ['A'])
        self.gateway.resolve_users.assert_not_called()
        self.assertIsNone(result['items']['A']['name'])
        self.assertFalse(result['user_details_enabled'])

    async def test_deduplicates_ids_and_reports_unresolved(self):
        service = self.service(account())
        result = await service.resolve_many(SimpleNamespace(id='account'), ['A', 'A', 'B'])
        self.assertEqual(self.gateway.resolve_users.call_args.args, ('account', ['A', 'B']))
        self.assertEqual(result['unresolved_count'], 1)
        self.assertFalse(result['profiles_complete'])
        self.assertIsNone(result['items']['B']['name'])

    async def test_wecom_shared_provider_does_not_schedule_title_updates(self):
        service = self.service(account('wecom'))
        result = await service.resolve_many(SimpleNamespace(id='account'), ['A'])
        self.assertEqual(result['items']['A']['userid'], 'member')
        self.assertTrue(result['profiles_complete'])
        self.assertEqual(self.provider.resolve.call_args.kwargs, {'await_refresh': True})

    async def test_disabled_during_lookup_discards_late_names(self):
        row = account()
        service = self.service(row)
        self.store.get_account.side_effect = [row, account(enabled=False)]
        result = await service.resolve_many(SimpleNamespace(id='account'), ['A'])
        self.assertIsNone(result['items']['A']['name'])
        self.assertFalse(result['user_details_enabled'])

    async def test_network_failure_and_timeout_are_not_empty_success(self):
        service = self.service(account())
        self.gateway.resolve_users.side_effect = TimeoutError()
        result = await service.resolve_many(SimpleNamespace(id='account'), ['A'])
        self.assertFalse(result['profiles_complete'])
        self.assertEqual(result['unresolved_count'], 1)

    async def test_history_response_enrichment_never_changes_snapshot_or_reuses_old_name(self):
        service = self.service(account())
        origin = {'account_id':'account','peer_type':'single','peer_id':'A',
                  'peer_metadata':{'display_name':'旧名','wecom_name':'旧名'}}
        result = await service.enrich_origins({'session':origin})
        self.assertEqual(result['session']['peer_metadata']['display_name'], '姓名')
        self.assertEqual(origin['peer_metadata']['display_name'], '旧名')
        self.gateway.resolve_users.side_effect = TimeoutError()
        result = await service.enrich_origins({'session':origin})
        self.assertNotIn('display_name', result['session']['peer_metadata'])
        self.assertNotIn('wecom_name', result['session']['peer_metadata'])
