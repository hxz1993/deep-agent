import asyncio
import json
import unittest

import httpx

from deep_agent.services.channel_registration import RegistrationError
from deep_agent.services.dingtalk_registration import DingtalkRegistrationService


class RegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []
        self.url = "https://qr.dingtalk.com/fixture"
        self.state = "SUCCESS"
        self.service = DingtalkRegistrationService(transport=httpx.MockTransport(self.handler), poll_interval=.001)
        self.addAsyncCleanup(self.service.aclose)

    def handler(self, request):
        self.requests.append(request)
        if request.url.path.endswith("/init"):
            data = {"nonce": "fixture-nonce"}
        elif request.url.path.endswith("/begin"):
            data = {"device_code": "fixture-code", "verification_uri_complete": self.url, "interval": .001}
        else:
            data = {"status": self.state, "client_id": "app", "client_secret": "private-fixture"}
        return httpx.Response(200, json=data)

    async def start(self):
        key = (await self.service.start("admin"))["registration_id"]
        for _ in range(100):
            if self.service.snapshot(key, "admin")["status"] not in {"starting", "waiting"}:
                break
            await asyncio.sleep(.002)
        return key

    async def test_protocol_ownership_and_consumption(self):
        key = await self.start()
        result = self.service.snapshot(key, "admin")
        self.assertEqual(result["status"], "authorized")
        self.assertNotIn("private-fixture", str(result))
        self.assertEqual(json.loads(self.requests[0].content), {"source": "DING_DWS_CLAW"})
        self.assertEqual(json.loads(self.requests[1].content), {"nonce": "fixture-nonce"})
        self.assertEqual(json.loads(self.requests[2].content), {"device_code": "fixture-code"})
        self.assertEqual(self.requests[2].extensions["timeout"]["read"], 180)
        with self.assertRaises(RegistrationError):
            self.service.snapshot(key, "other")
        async with self.service.credentials(key, "admin") as credentials:
            self.assertEqual(credentials, ("app", "private-fixture"))
        with self.assertRaises(RegistrationError):
            async with self.service.credentials(key, "admin"):
                pass

    async def test_failure_expiration_and_untrusted_url(self):
        for state, expected in (("FAIL", "failed"), ("EXPIRED", "expired")):
            self.state = state
            key = await self.start()
            self.assertEqual(self.service.snapshot(key, "admin")["status"], expected)
        self.url = "https://dingtalk.com.evil.invalid/qr"
        key = await self.start()
        self.assertEqual(self.service.snapshot(key, "admin")["status"], "failed")

    async def test_waiting_cancel_clears_secrets(self):
        self.state = "WAITING"
        key = await self.start()
        await self.service.cancel(key, "admin")
        self.assertEqual(self.service.snapshot(key, "admin")["status"], "cancelled")
        self.assertTrue(self.service.entries[key].task.done())


    async def test_transient_poll_timeout_and_creating_recover_with_save_window(self):
        attempts = 0
        gate = asyncio.Event()
        async def handler(request):
            nonlocal attempts
            if not request.url.path.endswith("/poll"):
                return self.handler(request)
            attempts += 1
            if attempts == 1:
                raise httpx.ReadTimeout("fixture", request=request)
            if attempts == 2:
                return httpx.Response(200, json={"status": "CREATING"})
            await gate.wait()
            return httpx.Response(200, json={"status": "SUCCESS", "client_id": "app", "client_secret": "fixture"})
        self.service.transport = httpx.MockTransport(handler)
        key = (await self.service.start("admin"))["registration_id"]
        for _ in range(100):
            if self.service.snapshot(key, "admin")["status"] == "creating":
                break
            await asyncio.sleep(.002)
        self.assertEqual(self.service.snapshot(key, "admin")["status"], "creating")
        self.assertIsNone(self.service.snapshot(key, "admin")["error"])
        gate.set()
        for _ in range(100):
            if self.service.snapshot(key, "admin")["status"] == "authorized":
                break
            await asyncio.sleep(.002)
        result = self.service.snapshot(key, "admin")
        self.assertEqual(result["status"], "authorized")
        self.assertGreaterEqual(result["expires_in"], 598)
        self.assertEqual(sum(r.url.path.endswith("/begin") for r in self.requests), 1)

    async def test_scanned_is_pending_then_success(self):
        gate = asyncio.Event()
        polls = 0
        async def handler(request):
            nonlocal polls
            if not request.url.path.endswith("/poll"):
                return self.handler(request)
            polls += 1
            if polls == 1:
                return httpx.Response(200, json={"errcode": 0, "status": "SCANNED"})
            await gate.wait()
            return httpx.Response(200, json={"errcode": 0, "status": "SUCCESS", "client_id": "app", "client_secret": "fixture"})
        self.service.transport = httpx.MockTransport(handler)
        key = (await self.service.start("admin"))["registration_id"]
        for _ in range(100):
            if self.service.snapshot(key, "admin")["status"] == "scanned": break
            await asyncio.sleep(.002)
        self.assertEqual(self.service.snapshot(key, "admin")["status"], "scanned")
        self.assertIsNone(self.service.snapshot(key, "admin")["error"])
        gate.set()
        for _ in range(100):
            if self.service.snapshot(key, "admin")["status"] == "authorized": break
            await asyncio.sleep(.002)
        self.assertEqual(self.service.snapshot(key, "admin")["status"], "authorized")

    async def test_retryable_http_poll_errors_keep_same_registration(self):
        statuses = iter([429, 502, 503, 200])
        async def handler(request):
            if not request.url.path.endswith('/poll'):
                return self.handler(request)
            self.requests.append(request)
            status = next(statuses)
            return httpx.Response(status, json={'status': 'SUCCESS', 'client_id': 'app', 'client_secret': 'fixture'})
        self.service.transport = httpx.MockTransport(handler)
        key = await self.start()
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'authorized')
        self.assertEqual(sum(r.url.path.endswith('/begin') for r in self.requests), 1)
        self.assertEqual([json.loads(r.content) for r in self.requests if r.url.path.endswith('/poll')], [{'device_code': 'fixture-code'}] * 4)

    async def test_permanent_http_poll_error_does_not_retry(self):
        polls = 0
        async def handler(request):
            nonlocal polls
            if not request.url.path.endswith('/poll'):
                return self.handler(request)
            polls += 1
            return httpx.Response(403, json={'message': 'private-fixture'})
        self.service.transport = httpx.MockTransport(handler)
        key = await self.start()
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'failed')
        self.assertEqual(polls, 1)
        self.assertNotIn('private-fixture', str(self.service.snapshot(key, 'admin')))

    async def test_platform_lifetime_bounds_inflight_poll_and_retries(self):
        for error in (False, True):
            async def handler(request):
                if request.url.path.endswith('/begin'):
                    return httpx.Response(200, json={'device_code': 'fixture-code', 'verification_uri_complete': self.url, 'expires_in': .02})
                if request.url.path.endswith('/poll'):
                    if error:
                        return httpx.Response(503)
                    await asyncio.sleep(1)
                    return httpx.Response(200, json={'status': 'SUCCESS', 'client_id': 'app', 'client_secret': 'late-secret'})
                return self.handler(request)
            self.service.transport = httpx.MockTransport(handler)
            key = (await self.service.start('admin'))['registration_id']
            # Await the task itself so snapshot expiry cannot conceal a leaked poll.
            await asyncio.wait_for(asyncio.shield(self.service.entries[key].task), .3)
            self.assertEqual(self.service.entries[key].status, 'expired')
            self.assertIsNone(self.service.entries[key].secret)

    async def test_success_without_credentials_is_not_authorized(self):
        async def handler(request):
            if request.url.path.endswith('/poll'):
                return httpx.Response(200, json={'status': 'SUCCESS', 'client_id': 'app'})
            return self.handler(request)
        self.service.transport = httpx.MockTransport(handler)
        key = await self.start()
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'failed')
        with self.assertRaises(RegistrationError):
            async with self.service.credentials(key, 'admin'):
                self.fail('missing credentials accepted')

    async def test_failed_save_preserves_credentials_for_retry(self):
        key = await self.start()
        with self.assertRaises(RuntimeError):
            async with self.service.credentials(key, 'admin'):
                raise RuntimeError('database save rejected')
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'authorized')
        async with self.service.credentials(key, 'admin') as credentials:
            self.assertEqual(credentials, ('app', 'private-fixture'))
        self.assertIsNone(self.service.entries[key].secret)
        self.assertTrue(self.service.entries[key].task.done())

    async def test_selecting_existing_application_stays_pending(self):
        self.state = 'SELECTING'
        key = await self.start()
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'selecting')
        self.assertIsNone(self.service.entries[key].secret)
        self.state = 'SUCCESS'
        for _ in range(100):
            if self.service.snapshot(key, 'admin')['status'] == 'authorized':
                break
            await asyncio.sleep(.002)
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'authorized')

    async def test_unknown_protocol_fields_are_not_logged(self):
        from unittest.mock import patch
        async def handler(request):
            if request.url.path.endswith('/poll'):
                return httpx.Response(200, json={'status': 'PRIVATE_SECRET', 'errcode': 'private-fixture'})
            return self.handler(request)
        self.service.transport = httpx.MockTransport(handler)
        with patch('deep_agent.services.dingtalk_registration.emit') as logged:
            key = await self.start()
        self.assertEqual(self.service.snapshot(key, 'admin')['status'], 'failed')
        self.assertNotIn('PRIVATE_SECRET', str(logged.call_args_list))
        self.assertNotIn('private-fixture', str(logged.call_args_list))

    async def test_cancel_aborts_inflight_poll_without_late_authorization(self):
        entered, cancelled = asyncio.Event(), asyncio.Event()
        async def handler(request):
            if not request.url.path.endswith('/poll'):
                return self.handler(request)
            entered.set()
            try:
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        self.service.transport = httpx.MockTransport(handler)
        key = (await self.service.start('admin'))['registration_id']
        await asyncio.wait_for(entered.wait(), .3)
        await self.service.cancel(key, 'admin')
        self.assertTrue(cancelled.is_set())
        result = self.service.snapshot(key, 'admin')
        self.assertEqual(result['status'], 'cancelled')
        self.assertIsNone(result['verification_url'])
        self.assertIsNone(self.service.entries[key].secret)
        self.assertTrue(self.service.entries[key].task.done())
