from __future__ import annotations

import unittest
import asyncio
from aiohttp import web
from deep_agent.security.console_admin import (
    ConsoleAdminAuthenticationError, ConsoleAdminUnavailableError,
    PlatformEnterpriseAdminChecker, forwarded_platform_auth_headers,
)
from tests.unit.test_console_admin_auth import _request


class ApiKeyAdminTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.identity = {"valid": True, "userId": "u1"}
        self.decision = True
        self.calls = []
        self.status = 200
        self.stream = False
        self.raw_body = None
        self.redirect = False
        self.headers = {"user": "u1", "x-deepfos-key": "secret-key", "app": "app1", "space": "space1"}

        async def handler(request):
            self.calls.append((request.path, dict(request.query), dict(request.headers)))
            if self.redirect:
                raise web.HTTPFound('/must-not-follow')
            if self.raw_body is not None:
                return web.Response(body=self.raw_body)
            if self.stream:
                response = web.StreamResponse()
                await response.prepare(request)
                if request.path.endswith("checkApiKey"):
                    await response.write(b'{"valid":true,')
                    await asyncio.sleep(0.01)
                    await response.write(b'"userId":"u1"}')
                else:
                    await response.write(b'tr')
                    await asyncio.sleep(0.01)
                    await response.write(b'ue')
                await response.write_eof()
                return response
            return web.json_response(
                self.identity if request.path.endswith("checkApiKey") else self.decision,
                status=self.status,
            )
        app = web.Application()
        app.router.add_get('/oauth/{method}', handler)
        self.runner = web.AppRunner(app, access_log=None)
        await self.runner.setup()
        site = web.TCPSite(self.runner, '127.0.0.1', 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.checker = PlatformEnterpriseAdminChecker(account_service=f'http://127.0.0.1:{port}')

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_key_owner_does_not_require_admin_or_forward_user(self):
        self.decision = False
        owner = await self.checker.resolve_api_key_user("fixture-key", "app1", "space1")
        self.assertEqual(owner, "u1")
        self.assertEqual([c[0] for c in self.calls], ["/oauth/checkApiKey"])
        self.assertNotIn("user", {k.lower(): v for k, v in self.calls[0][2].items()})
        self.assertNotIn("Cookie", self.calls[0][2])

    async def test_owner_lookup_rejects_invalid_malformed_and_upstream_errors(self):
        for identity, expected in (({"valid": False}, ConsoleAdminAuthenticationError),
                                   ({"valid": True}, ConsoleAdminUnavailableError),
                                   ({"valid": True, "userId": "bad\nuser"}, ConsoleAdminUnavailableError)):
            self.identity = identity
            with self.assertRaises(expected) as raised:
                await self.checker.resolve_api_key_user("fixture-key", "app1", "space1")
            self.assertNotIn("fixture-key", str(raised.exception))
        self.status = 503
        with self.assertRaises(ConsoleAdminUnavailableError):
            await self.checker.resolve_api_key_user("fixture-key", "app1", "space1")
        self.redirect = True
        with self.assertRaises(ConsoleAdminUnavailableError):
            await self.checker.resolve_api_key_user("fixture-key", "app1", "space1")
        self.assertTrue(all(c[0] == "/oauth/checkApiKey" for c in self.calls))

    async def test_owner_rejection_reasons_are_distinct_and_sanitized(self):
        from deep_agent.security.console_admin import PlatformApiKeyRejectedError
        for reason, message in (
            ("密钥不存在", "不存在"),
            ("密钥与空间不匹配", "所属空间"),
            ("密钥授权过期", "已停用或过期"),
            ("密钥未授权该应用", "未授权当前关联应用"),
            ("upstream-secret-fixture", "未返回可识别的原因"),
            ({"unexpected": "data"}, "未返回可识别的原因"),
        ):
            with self.subTest(reason=reason):
                self.identity = {"valid": False, "errorMsg": reason}
                with self.assertRaises(PlatformApiKeyRejectedError) as raised:
                    await self.checker.resolve_api_key_user("fixture-key", "app1", "space1")
                self.assertIn(message, raised.exception.public_message)
                self.assertNotIn("upstream-secret-fixture", str(raised.exception))
                self.assertNotIn("fixture-key", str(raised.exception))

    async def test_scoped_key_then_admin_check(self):
        self.assertTrue(await self.checker('u1', self.headers))
        self.assertEqual([c[0] for c in self.calls], ['/oauth/checkApiKey', '/oauth/checkEnterpriseAdmin'])
        self.assertEqual(self.calls[0][1], {'apiKey': 'secret-key', 'app': 'app1', 'space': 'space1'})
        self.assertEqual(self.calls[1][1], {'userId': 'u1'})
        self.assertNotIn('Cookie', self.calls[0][2])

    async def test_chunked_response_reads_until_eof(self):
        self.stream = True
        self.assertTrue(await self.checker("u1", self.headers))

    async def test_redirect_is_not_followed(self):
        self.redirect = True
        with self.assertRaises(ConsoleAdminUnavailableError):
            await self.checker('u1', self.headers)
        self.assertEqual(len(self.calls), 1)

    async def test_ambiguous_and_oversized_payloads_are_rejected(self):
        for body in (b'{"valid":false,"valid":true,"userId":"u1"}', b' ' * 65537):
            self.raw_body = body
            with self.assertRaises(ConsoleAdminUnavailableError):
                await self.checker('u1', self.headers)

    async def test_valid_non_admin_denied(self):
        self.decision = False
        self.assertFalse(await self.checker('u1', self.headers))

    async def test_invalid_and_mismatched_identity_never_check_roles(self):
        for identity in ({'valid': False}, {'valid': True, 'userId': 'another-user'}):
            self.calls.clear()
            self.identity = identity
            with self.assertRaises(ConsoleAdminAuthenticationError):
                await self.checker('u1', self.headers)
            self.assertEqual(len(self.calls), 1)

    async def test_bad_payload_and_service_failure_fail_closed(self):
        for identity in ({'valid': 'true', 'userId': 'u1'}, {'valid': True}, {'valid': True, 'userId': 1}):
            self.identity = identity
            with self.assertRaises(ConsoleAdminUnavailableError) as error:
                await self.checker('u1', self.headers)
            self.assertNotIn('secret-key', str(error.exception))
            self.assertTrue(error.exception.__suppress_context__)
        self.status = 401
        with self.assertRaises(ConsoleAdminAuthenticationError):
            await self.checker('u1', self.headers)
        self.status = 503
        with self.assertRaises(ConsoleAdminUnavailableError):
            await self.checker('u1', self.headers)

    async def test_wrapped_responses(self):
        self.identity = {'status': True, 'data': self.identity}
        self.decision = {'status': True, 'data': True}
        self.assertTrue(await self.checker('u1', self.headers))

    def test_scope_forwarding_and_mixed_credential_rejection(self):
        forwarded = forwarded_platform_auth_headers(_request(**self.headers), 'u1')
        self.assertEqual(forwarded, self.headers)
        for extra in ({'token': 'token'}, {'cookie': 'session=token'}, {'authorization': 'Bearer token'}, {'app': ''}, {'space': ''}):
            with self.subTest(extra=extra), self.assertRaises(ConsoleAdminAuthenticationError):
                forwarded_platform_auth_headers(_request(**{**self.headers, **extra}), 'u1')
        forwarded = forwarded_platform_auth_headers(_request(token='token', app='app1', space='space1', **{'x-deepfos-untrusted': 'bad'}), 'u1')
        self.assertEqual(forwarded, {'token': 'token', 'user': 'u1'})

    def test_duplicate_credentials_rejected(self):
        request = _request(**self.headers)
        request.scope['headers'].append((b'x-deepfos-key', b'other-key'))
        with self.assertRaises(ConsoleAdminAuthenticationError):
            forwarded_platform_auth_headers(request, 'u1')
