from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from deep_agent.services.feishu_permissions import check_user_details_permissions, NAME_SCOPE


class Response:
    status = 200
    def __init__(self, body): self.body = body
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    async def json(self, **kwargs): return self.body


class Session:
    def __init__(self, scopes): self.scopes = scopes
    async def __aenter__(self): return self
    async def __aexit__(self, *args): pass
    def post(self, *args, **kwargs):
        return Response({'code': 0, 'tenant_access_token': 'test-only'})
    def get(self, *args, **kwargs):
        return Response({'code': 0, 'data': {'scopes': self.scopes}})


class PermissionTests(IsolatedAsyncioTestCase):
    async def check(self, scopes):
        with patch('deep_agent.services.feishu_permissions.aiohttp.ClientSession', return_value=Session(scopes)):
            return await check_user_details_permissions(SimpleNamespace(bot_id='cli_test', secret='test-only'))

    async def test_only_granted_tenant_permission_authorizes(self):
        for kind, grant, expected in [('user',1,'missing'),('tenant',0,'missing'),('tenant',1,'authorized')]:
            result = await self.check([{'scope_name': NAME_SCOPE, 'scope_type': kind, 'grant_status': grant}])
            self.assertEqual(result['status'], expected)
            self.assertNotIn('test-only', str(result))

    async def test_other_contacts_permission_does_not_claim_name_field_granted(self):
        result = await self.check([{'scope_name':'contact:contact.base:readonly','scope_type':'tenant','grant_status':1}])
        self.assertEqual(result['status'], 'missing')
        self.assertEqual(result['permissions'][0]['scope'], NAME_SCOPE)

    async def test_network_error_is_not_missing_permission(self):
        with patch('deep_agent.services.feishu_permissions.aiohttp.ClientSession', side_effect=TimeoutError):
            result = await check_user_details_permissions(SimpleNamespace(bot_id='cli_test', secret='test-only'))
        self.assertEqual(result['status'], 'error')
        self.assertEqual(result['permissions'], [])
