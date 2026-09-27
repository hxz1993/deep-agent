from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from deep_agent.channels.gateway.runtime import ChannelRuntime
from deep_agent.channels.registry import AdapterRegistry, default_registry


class RegistryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    def test_default_registry_installs_feishu_and_tracks_connection_settings(self):
        from deep_agent.channels.adapters.feishu import FeishuAdapter
        registry = default_registry()
        account = SimpleNamespace(id="feishu", channel_type="feishu", transport="websocket",
                                  bot_id="cli_fixture", secret="fixture", settings={})
        self.assertIsInstance(registry.create(account), FeishuAdapter)
        original = registry.fingerprint(account)
        account.name = "renamed"
        self.assertEqual(original, registry.fingerprint(account))
        account.settings = {"feishu": {"group_allowlist": ["oc_fixture"]}}
        self.assertNotEqual(original, registry.fingerprint(account))

    async def test_common_host_routes_test_adapter_and_keeps_business_edits_connected(self):
        adapters = []
        account = SimpleNamespace(id="test-account", channel_type="test-transport", enabled=True,
                                  credential="fixture", agent_id="first", session_user_id="user", name="first")

        def factory(_account, **_kwargs):
            adapter = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(),
                                      status=lambda: {"state": "connected"})
            adapters.append(adapter)
            return adapter

        registry = AdapterRegistry()
        registry.register("test-transport", factory, lambda row: row.credential)
        store = SimpleNamespace(list_accounts=AsyncMock(return_value=[account]))
        runtime = ChannelRuntime(store, SimpleNamespace(stream_interval=0.1), registry=registry, deep_agent=SimpleNamespace())
        runtime.service.close_account = AsyncMock()
        runtime.service.receive = AsyncMock()
        await runtime.reconcile()
        account.agent_id, account.name = "second", "renamed"
        await runtime.reconcile()
        self.assertEqual(len(adapters), 1)
        callback = adapters[0].start.call_args.args[0]
        opaque_message = object()
        await callback(opaque_message)
        runtime.service.receive.assert_awaited_once_with(account, adapters[0], opaque_message)
        account.credential = "changed-fixture"
        await runtime.reconcile()
        self.assertEqual(len(adapters), 2)
        adapters[0].stop.assert_awaited_once()
        account.enabled = False
        await runtime.reconcile()
        adapters[1].stop.assert_awaited_once()
        self.assertEqual(runtime._accounts, {})

    async def test_adapter_still_closes_when_business_cleanup_fails(self):
        registry = AdapterRegistry()
        adapter = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(), status=lambda: {})
        registry.register("test", lambda *_args, **_kwargs: adapter, lambda _: "fixture")
        account = SimpleNamespace(id="account", channel_type="test", enabled=True)
        store = SimpleNamespace(list_accounts=AsyncMock(return_value=[account]))
        runtime = ChannelRuntime(store, SimpleNamespace(stream_interval=0.1), registry=registry, deep_agent=SimpleNamespace())
        await runtime.reconcile()
        runtime.service.close_account = AsyncMock(side_effect=RuntimeError("fixture DB failure"))
        with self.assertRaises(RuntimeError):
            await runtime._stop_account(account.id)
        adapter.stop.assert_awaited_once()

    async def test_failed_close_retains_client_until_retry_succeeds(self):
        registry = AdapterRegistry()
        adapter = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(side_effect=RuntimeError("close failed")), status=lambda: {})
        factory = lambda *_args, **_kwargs: adapter
        registry.register("test", factory, lambda _: "fixture")
        account = SimpleNamespace(id="account", channel_type="test", enabled=True)
        runtime = ChannelRuntime(SimpleNamespace(list_accounts=AsyncMock(return_value=[account])),
                                 SimpleNamespace(stream_interval=0.1), registry=registry, deep_agent=SimpleNamespace())
        await runtime.reconcile()
        with self.assertRaises(RuntimeError):
            await runtime._stop_account(account.id)
        self.assertTrue(runtime._accounts[account.id].stopping)
        adapter.stop.side_effect = None
        await runtime._stop_account(account.id)
        self.assertEqual(runtime._accounts, {})

    async def test_feishu_credential_test_does_not_open_message_connection(self):
        adapter = SimpleNamespace(start=AsyncMock(), stop=AsyncMock(),
                                  test_connection=AsyncMock(return_value={"ok": True}))
        registry = AdapterRegistry()
        registry.register("feishu", lambda *_a, **_k: adapter, lambda _: "fixture")
        account = SimpleNamespace(id="account", channel_type="feishu", enabled=False)
        store = SimpleNamespace(list_accounts=AsyncMock(return_value=[]),
                                get_account=AsyncMock(return_value=account))
        runtime = ChannelRuntime(store, SimpleNamespace(stream_interval=0.1, test_timeout=5),
                                 registry=registry, deep_agent=SimpleNamespace())
        self.assertTrue((await runtime.test_account("account"))["ok"])
        adapter.start.assert_not_awaited()
        adapter.test_connection.assert_awaited_once_with(5)
        adapter.stop.assert_awaited_once()
