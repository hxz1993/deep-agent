from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from deep_agent.schemas.channel import ChannelAccountCreate, ChannelAccountUpdate
from deep_agent.services.wecom_client import WecomClient, WecomError
from deep_agent.services.wecom_profiles import WecomProfileService
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class WecomClientTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.client = WecomClient()
        self.app = SimpleNamespace(id="app", revision=1, corp_id="corp", app_secret="fixture-secret")

    async def asyncTearDown(self):
        await self.client.close()

    async def test_concurrent_token_fetch_and_explicit_invalid_retry(self):
        self.client._request = AsyncMock(return_value={"errcode": 0, "access_token": "token", "expires_in": 7200})
        self.assertEqual(await asyncio.gather(*(self.client.token(self.app) for _ in range(20))), ["token"] * 20)
        self.assertEqual(self.client._request.await_count, 1)
        self.client._request.reset_mock()
        self.client._request.side_effect = [{"errcode": 42001}, {"access_token": "new-token", "expires_in": 7200}, {"errcode": 0, "userid": "Member", "name": "姓名"}]
        self.assertEqual(await self.client.member(self.app, "member"), ("Member", "姓名"))
        self.assertEqual(self.client._request.await_count, 3)

    async def test_business_failure_does_not_refresh_token_or_probe_plaintext(self):
        self.client._request = AsyncMock(side_effect=[{"access_token": "t", "expires_in": 7200}, {"errcode": 60020, "errmsg": "secret upstream details"}])
        with self.assertRaises(WecomError) as raised:
            await self.client.convert(self.app, "opaque")
        self.assertEqual(raised.exception.code, "60020")
        self.assertNotIn("upstream", str(raised.exception))
        self.assertEqual(self.client._request.await_count, 2)

    async def test_only_explicit_invalid_id_allows_plaintext_probe(self):
        self.client.call = AsyncMock(return_value={"errcode": 0, "userid_list": [], "invalid_open_userid_list": ["other"]})
        self.assertEqual(await self.client.convert(self.app, "raw"), (None, False))
        self.client.call.return_value["invalid_open_userid_list"] = ["raw"]
        self.assertEqual(await self.client.convert(self.app, "raw"), (None, True))
        self.client.call.return_value = {"errcode": 0, "userid": "different", "name": "Name"}
        with self.assertRaises(WecomError):
            await self.client.member(self.app, "raw")

    async def test_token_invalidation_is_safe_error_but_caller_cancellation_stays_cancelled(self):
        entered = asyncio.Event()
        async def slow(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()
        self.client._request = slow
        caller = asyncio.create_task(self.client.token(self.app))
        await entered.wait()
        self.client.invalidate(self.app.id)
        with self.assertRaises(WecomError) as raised:
            await caller
        self.assertEqual(raised.exception.code, "configuration_changed")
        entered.clear()
        caller = asyncio.create_task(self.client.token(self.app))
        await entered.wait()
        caller.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await caller

    async def test_malformed_upstream_fields_never_become_error_text_or_plaintext_probe(self):
        for data in ({"errcode": "private-upstream-content"},
                     {"access_token": "test-token", "expires_in": "private-upstream-content"},
                     {"access_token": "test-token", "expires_in": float("inf")}):
            self.client._request = AsyncMock(return_value=data)
            with self.assertRaises(WecomError) as raised:
                await self.client.token(self.app)
            self.assertNotIn("private-upstream", str(raised.exception))
        for data in ({"userid_list": None}, {"invalid_open_userid_list": None},
                     {"userid_list": [{"open_userid": "raw", "userid": {"private": "value"}}]}):
            self.client.call = AsyncMock(return_value=data)
            with self.assertRaises(WecomError):
                await self.client.convert(self.app, "raw")

    async def test_connection_probe_refreshes_token_without_cancelling_shared_work(self):
        self.client._request = AsyncMock(return_value={"access_token": "token", "expires_in": 7200})
        await self.client.token(self.app)
        await self.client.token(self.app, force=True)
        self.assertEqual(self.client._request.await_count, 2)


class WecomProfileTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        agent = await seed_default_agent(self.stores)
        self.app = await self.stores.wecom.create_application(name="应用", corp_id="corp", wecom_agent_id="1", app_secret="fixture-secret")
        self.account = await self.stores.channel.create_account(ChannelAccountCreate(
            name="机器人", bot_id="bot", secret="fixture-secret", agent_id=agent, session_user_id="owner", enabled=True,
            settings={"wecom": {"user_details_enabled": True, "application_id": self.app.id}},
        ))
        self.client = SimpleNamespace(convert=AsyncMock(return_value=("Member", False)), member=AsyncMock(return_value=("Member", "成员姓名")), close=AsyncMock(), invalidate=lambda app: None)
        self.service = WecomProfileService(self.stores.wecom, self.stores.channel, client=self.client)

    async def asyncTearDown(self):
        await self.service.close()
        await self.engine.dispose()

    async def resolve(self, raw="opaque"):
        return await self.service.resolve(self.account, raw, peer_type="group")

    async def test_disabled_defaults_no_external_calls(self):
        for settings in ({}, {"wecom": {"application_id": self.app.id}}, {"wecom": {"user_details_enabled": False, "application_id": self.app.id}}):
            self.account.settings = settings
            self.assertEqual((await self.resolve())["status"], "disabled")
        self.client.convert.assert_not_awaited()

    async def test_cipher_then_plain_share_record_and_cache(self):
        results = await asyncio.gather(*(self.resolve() for _ in range(12)))
        self.assertTrue(all(r["name"] == "成员姓名" for r in results))
        self.assertEqual(self.client.convert.await_count, 1)
        self.assertEqual(self.client.member.await_count, 1)
        self.assertEqual((await self.resolve("member"))["userid"], "Member")
        stats = await self.stores.wecom.cache_stats(self.app.id)
        self.assertEqual((stats["total"], stats["pending"]), (1, 0))

    async def test_plain_first_then_cipher_merges(self):
        self.client.convert.return_value = (None, True)
        await self.resolve("Member")
        self.client.convert.return_value = ("Member", False)
        await self.resolve("opaque")
        profiles = await self.stores.wecom.list_profiles(self.app.id)
        self.assertEqual(len(profiles), 1)
        self.assertEqual(profiles[0].open_userid, "opaque")

    async def test_unknown_failure_negative_cache_and_no_placeholder(self):
        self.client.convert.side_effect = WecomError("convert", 60020)
        await self.resolve()
        await self.resolve()
        self.assertEqual(self.client.convert.await_count, 1)
        self.client.member.assert_not_awaited()
        self.assertEqual((await self.stores.wecom.cache_stats(self.app.id))["total"], 0)
        self.service.invalidate_runtime(self.app.id)
        await self.resolve()
        self.assertEqual(self.client.convert.await_count, 2)

    async def test_confirmed_identity_missing_name_remains_pending(self):
        self.client.member.return_value = ("Member", None)
        result = await self.resolve()
        self.assertEqual((result["userid"], result["name"]), ("Member", None))
        self.assertEqual((await self.stores.wecom.cache_stats(self.app.id))["pending"], 1)

    async def test_timeout_continues_background_and_configuration_fences_result(self):
        barrier = asyncio.Event()
        async def member(*args):
            await barrier.wait()
            return "Member", "迟到姓名"
        self.client.member.side_effect = member
        self.service.wait_seconds = .01
        result = await self.resolve()
        self.assertIsNone(result["name"])
        await self.stores.wecom.invalidate_cache(self.app.id)
        barrier.set()
        await asyncio.gather(*list(self.service._queries.values()))
        profile = await self.stores.wecom.find_profile(self.app.id, "opaque")
        self.assertIsNone(profile.name)

    async def test_close_switch_rechecked_after_query(self):
        async def member(*args):
            await self.stores.channel.update_account(self.account.id, ChannelAccountUpdate(settings={"wecom": {"user_details_enabled": False, "application_id": self.app.id}}))
            return "Member", "成员姓名"
        self.client.member.side_effect = member
        result = await self.resolve()
        self.assertEqual(result["status"], "disabled")
        self.assertIsNone(result["name"])

    async def test_reset_preserves_name_and_bulk_refresh_updates_stats(self):
        await self.resolve()
        await self.stores.wecom.invalidate_cache(self.app.id)
        self.assertEqual((await self.stores.wecom.cache_stats(self.app.id))["pending"], 1)
        self.assertEqual((await self.stores.wecom.find_profile(self.app.id, "opaque")).name, "成员姓名")
        job = await self.service.start_refresh(self.app.id)
        repeated = await self.service.start_refresh(self.app.id)
        self.assertEqual(job["task_id"], repeated["task_id"])
        await asyncio.gather(*list(self.service._bulk_tasks.values()))
        result = self.service.refresh_status(self.app.id, job["task_id"])
        self.assertEqual((result["status"], result["succeeded"], result["failed"]), ("completed", 1, 0))
        self.assertEqual((await self.stores.wecom.cache_stats(self.app.id))["pending"], 0)
        self.assertEqual(self.client.convert.await_count, 1)

    async def test_background_hard_deadline(self):
        self.service.task_seconds = .02
        self.service.wait_seconds = .01
        self.client.convert.side_effect = lambda *args: None
        async def slow(*args):
            await asyncio.Event().wait()
        self.client.convert.side_effect = slow
        await self.resolve()
        tasks = list(self.service._queries.values())
        await asyncio.gather(*tasks)
        self.assertEqual(len(self.service._queries), 0)

    async def test_raw_to_userid_transition_reuses_pending_name_query(self):
        entered, release = asyncio.Event(), asyncio.Event()
        async def member(*args):
            entered.set()
            await release.wait()
            return "Member", "成员姓名"
        self.client.member.side_effect = member
        first = asyncio.create_task(self.resolve("opaque"))
        try:
            await entered.wait()
            second = asyncio.create_task(self.resolve("member"))
            await asyncio.sleep(.01)
            release.set()
            results = await asyncio.gather(first, second)
        finally:
            release.set()
            await asyncio.gather(first, return_exceptions=True)
        self.assertEqual(self.client.member.await_count, 1)
        self.assertTrue(all(result["name"] == "成员姓名" for result in results))
        self.assertFalse(self.service._queries)

    async def test_bulk_concurrent_start_has_one_snapshot_and_does_not_include_new_users(self):
        await self.resolve()
        entered, release = asyncio.Event(), asyncio.Event()
        async def member(*args):
            entered.set()
            await release.wait()
            return "Member", "新姓名"
        self.client.member.side_effect = member
        self.client.invalidate = AsyncMock()
        jobs = await asyncio.gather(*(self.service.start_refresh(self.app.id) for _ in range(6)))
        self.assertEqual(len({job["task_id"] for job in jobs}), 1)
        try:
            await entered.wait()
            await self.stores.wecom.upsert_identity(self.app.id, self.app.revision, "AfterStart")
        finally:
            release.set()
            await asyncio.gather(*list(self.service._bulk_tasks.values()))
        job = self.service.refresh_status(self.app.id, jobs[0]["task_id"])
        self.assertEqual((job["total"], job["completed"], job["succeeded"]), (1, 1, 1))
        self.client.invalidate.assert_not_called()

    async def test_negative_cache_is_bounded_and_expiry_is_removed(self):
        for index in range(2050):
            self.service._remember_failure((self.app.id, 1, "raw", str(index)))
        self.assertEqual(len(self.service._negative), 2048)
        self.service._negative[(self.app.id, 1, "raw", "2049")] = 0
        self.assertFalse(self.service._negative_hit((self.app.id, 1, "raw", "2049")))
        self.assertEqual(len(self.service._negative), 2047)
