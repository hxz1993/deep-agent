"""Source fields describe creation, without extending session ownership."""
import asyncio
import unittest

from sqlalchemy import update
from pydantic_ai.messages import ModelRequest, UserPromptPart

from deep_agent.core.context import get_context
from deep_agent.db.models import ChatSession
from tests.unit.test_session_list import SessionListApiTests


class SessionSourceApiTests(unittest.TestCase):
    setUp = SessionListApiTests.setUp
    tearDown = SessionListApiTests.tearDown

    def test_source_filter_is_persisted_and_owner_scoped(self):
        async def seed():
            for sid, owner in [('legacy', self.user_id), ('channel', self.user_id), ('foreign', 'other-user')]:
                await get_context().store.append(sid, [ModelRequest(parts=[UserPromptPart(content='question')])],
                    agent_id=self.agent_a, user_id=owner, turn_id=sid, run_id=sid)
            async with self.session_factory() as db:
                await db.execute(update(ChatSession).where(ChatSession.session_id=='legacy').values(source_type=None))
                await db.execute(update(ChatSession).where(ChatSession.session_id.in_(['channel','foreign'])).values(
                    source_type='channel:dingtalk', source_id='deleted-account'))
                await db.commit()
        asyncio.run(seed())
        params={'source_type':'channel:dingtalk','source_id':'deleted-account'}
        response=self.client.get('/api/v1/sessions',params=params)
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(response.json()['total'],1)
        row=response.json()['items'][0]
        self.assertEqual(row['session_id'],'channel')
        self.assertEqual(row['source_type'],'channel:dingtalk')
        self.assertEqual(row['source_id'],'deleted-account')
        self.assertTrue(row['channel']['account_deleted'])
        self.assertIsNone(row['channel']['peer_id'])
        detail=self.client.get('/api/v1/sessions/channel')
        self.assertEqual(detail.status_code,200,detail.text)
        self.assertEqual(detail.json()['source_id'],'deleted-account')
        internal=self.client.get('/api/v1/sessions',params={'source_type':'internal'}).json()
        self.assertEqual([r['session_id'] for r in internal['items']],['legacy'])
        self.assertEqual(internal['items'][0]['source_type'],'internal')
        self.assertIsNone(internal['items'][0]['channel'])
        self.assertEqual(self.client.get('/api/v1/sessions/foreign').status_code,404)
        self.assertEqual(self.client.get('/api/v1/sessions',params={'source_type':'channel:fake'}).status_code,422)

    def test_console_append_does_not_change_channel_source_or_title(self):
        async def scenario():
            store=get_context().store
            await store.append('source-stable',[ModelRequest(parts=[UserPromptPart(content='first')])],
                agent_id=self.agent_a,user_id=self.user_id,turn_id='one',run_id='one')
            async with self.session_factory() as db:
                await db.execute(update(ChatSession).where(ChatSession.session_id=='source-stable').values(
                    source_type='channel:feishu',source_id='original-account',title='original title'))
                await db.commit()
            await store.append('source-stable',[ModelRequest(parts=[UserPromptPart(content='console followup')])],
                agent_id=self.agent_a,user_id=self.user_id,turn_id='two',run_id='two')
        asyncio.run(scenario())
        row=self.client.get('/api/v1/sessions/source-stable').json()
        self.assertEqual((row['source_type'],row['source_id'],row['title']),
                         ('channel:feishu','original-account','original title'))

    def test_wecom_app_source_filter_across_public_apis(self):
        from tests.helpers.admin_auth import allow_console_admin
        from unittest.mock import AsyncMock, patch
        allow_console_admin(self.app)
        async def seed():
            for sid in ('app-session', 'internal-session'):
                await get_context().store.append(sid, [ModelRequest(parts=[UserPromptPart(content='question')])],
                    agent_id=self.agent_a, user_id=self.user_id, turn_id=sid, run_id=sid)
            async with self.session_factory() as db:
                await db.execute(update(ChatSession).where(ChatSession.session_id == 'app-session').values(
                    source_type='channel:wecom_app', source_id='deleted-app-channel'))
                await db.commit()
        asyncio.run(seed())
        params = {'source_type': 'channel:wecom_app', 'source_id': 'deleted-app-channel'}
        with patch('deep_agent.security.diagnostic_users.resolve_diagnostic_users', AsyncMock(return_value={})), \
             patch('deep_agent.api.v1.diagnostic_usage.resolve_diagnostic_users', AsyncMock(return_value={})):
            for path in ('/api/v1/sessions', '/api/v1/diagnostics/sessions'):
                response = self.client.get(path, params=params)
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual([row['session_id'] for row in response.json()['items']], ['app-session'])
                self.assertEqual(response.json()['items'][0]['source_type'], 'channel:wecom_app')
            response = self.client.get('/api/v1/diagnostics/usage', params=params)
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual(self.client.get('/api/v1/diagnostics/usage', params={'source_type':'channel:unknown'}).status_code, 422)
        paths = self.app.openapi()['paths']
        for path in ('/api/v1/sessions', '/api/v1/diagnostics/sessions', '/api/v1/diagnostics/usage'):
            parameter = next(row for row in paths[path]['get']['parameters'] if row['name'] == 'source_type')
            self.assertIn('channel:wecom_app', str(parameter['schema']))
