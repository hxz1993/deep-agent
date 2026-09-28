from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from deep_agent.channels.adapters.feishu.profiles import FeishuProfiles
from deep_agent.channels.adapters.feishu.core import FeishuSDKAdapter
from deep_agent.channels.app_context import validate_channel_settings
from deep_agent.channels.gateway.runtime import AccountRuntime, ChannelRuntime


class ProfileCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_overlap_coalesces_and_batches_and_reuses_names(self):
        calls = []
        async def lookup(ids):
            calls.append(ids)
            await asyncio.sleep(.001)
            return {uid: "同名" for uid in ids}
        cache = FeishuProfiles(lookup)
        ids = [str(i) for i in range(103)]
        a, b = await asyncio.gather(cache.resolve(ids), cache.resolve(ids[10:]))
        self.assertEqual(len(a), 103)
        self.assertEqual(len(b), 93)
        self.assertEqual([len(batch) for batch in calls], [50, 50, 3])
        await cache.resolve(ids)
        self.assertEqual(len(calls), 3)

    async def test_capacity_negative_backoff_and_no_cross_account_cache(self):
        lookup = AsyncMock(return_value={"a": "A", "b": "B", "c": "C"})
        cache = FeishuProfiles(lookup)
        cache.capacity = 2
        await cache.resolve(["a", "b"])
        await cache.resolve(["a"])
        await cache.resolve(["c"])
        self.assertEqual(cache.cached(["a", "b", "c"]), {"a": "A", "c": "C"})
        lookup.return_value = {}
        await cache.resolve(["missing"])
        calls = lookup.await_count
        await cache.resolve(["missing"])
        self.assertEqual(lookup.await_count, calls)
        cache.clear()
        await cache.resolve(["missing"])
        self.assertEqual(lookup.await_count, calls + 1)
        other = FeishuProfiles(AsyncMock(return_value={}))
        self.assertEqual(other.cached(["a"]), {})

    async def test_disabled_never_queries_and_sdk_callback_shares_cache_across_loops(self):
        account = SimpleNamespace(id="a", bot_id="app", secret="secret", settings={})
        adapter = FeishuSDKAdapter(account)
        lookup = AsyncMock(return_value={"u": "用户"})
        adapter._profiles.lookup = lookup
        adapter._started = True
        adapter._loop = asyncio.get_running_loop()
        self.assertEqual(await adapter._lookup_sender_names(["u"]), {})
        self.assertEqual((await adapter.resolve_users(["u"]))["items"][0]["status"], "disabled")
        lookup.assert_not_awaited()
        adapter._profiles_enabled = True
        result = await asyncio.to_thread(lambda: asyncio.run(adapter._lookup_sender_names(["u"])))
        self.assertEqual(result, {"u": "用户"})
        self.assertEqual((await adapter.resolve_users(["u"]))["items"][0]["name"], "用户")
        lookup.assert_awaited_once()

    async def test_cancel_releases_lookup_lock_without_negative_cache(self):
        event = asyncio.Event()
        async def wait(_):
            await event.wait()
            return {"u": "name"}
        cache = FeishuProfiles(wait)
        task = asyncio.create_task(cache.resolve(["u"]))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        event.set()
        self.assertEqual(await cache.resolve(["u"]), {"u": "name"})

    async def test_budget_returns_already_resolved_partial_without_waiting_for_slow_batch(self):
        account = SimpleNamespace(id="a", bot_id="app", secret="secret",
                                  settings={"feishu": {"user_details_enabled": True}})
        adapter = FeishuSDKAdapter(account)
        adapter._started = True
        adapter._profiles.batch_size = 1
        async def lookup(ids):
            if ids == ["slow"]:
                await asyncio.sleep(10)
            return {ids[0]: "name"}
        adapter._profiles.lookup = lookup
        result = await adapter.resolve_users(["fast", "slow"], timeout=.02)
        self.assertEqual(result["items"][0]["name"], "name")
        self.assertIsNone(result["items"][1]["name"])
        self.assertFalse(result["complete"])

    def test_opt_in_is_boolean_only(self):
        validate_channel_settings("feishu", {})
        validate_channel_settings("feishu", {"feishu": {"user_details_enabled": True}})
        for value in (1, "true", None):
            with self.assertRaises(ValueError):
                validate_channel_settings("feishu", {"feishu": {"user_details_enabled": value}})


class RuntimeProfileTests(unittest.IsolatedAsyncioTestCase):
    async def test_fresh_gate_and_late_configuration_change(self):
        account = SimpleNamespace(id="a", enabled=True, channel_type="feishu",
                                  settings={"feishu": {"user_details_enabled": True}})
        store = SimpleNamespace(get_account=AsyncMock(return_value=account))
        registry = SimpleNamespace(fingerprint=lambda a: str(a.settings))
        runtime = ChannelRuntime(store, SimpleNamespace(stream_interval=.1), registry=registry,
                                 deep_agent=SimpleNamespace())
        result = {"items": [{"sender_id": "u", "name": "新名", "userid": None, "status": "resolved"}], "complete": True}
        adapter = SimpleNamespace(resolve_users=AsyncMock(return_value=result))
        managed = AccountRuntime(account, adapter, registry.fingerprint(account))
        runtime._accounts["a"] = managed
        self.assertEqual(await runtime.resolve_users("a", ["u"]), result)
        async def late(_, **kwargs):
            account.enabled = False
            return result
        adapter.resolve_users.side_effect = late
        self.assertIsNone((await runtime.resolve_users("a", ["u"]))["items"][0]["name"])
        calls = adapter.resolve_users.await_count
        await runtime.resolve_users("a", ["u"])
        self.assertEqual(adapter.resolve_users.await_count, calls)
