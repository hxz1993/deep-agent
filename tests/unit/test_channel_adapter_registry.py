from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock

from pydantic_ai import RunContext
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.channels.doc_tool import build_channel_doc
from deep_agent.channels.gateway.runtime import ChannelRuntime
from deep_agent.channels.instructions import channel_instructions
from deep_agent.channels.registry import AdapterRegistry, default_registry


class RegistryRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_channel_docs_match_each_adapter_and_do_not_mix_platforms(self):
        registry = default_registry()
        settings = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(version=1, value={"max_size_mb": 50})))
        deps = AgentDeps(session_id="fixture", agent_id="agent", user="user", upload_settings_store=settings)
        ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage())
        cases = {
            "wecom": ("企业微信机器人", "图片：PNG、JPEG，最多 10 MiB", "引用文字及支持的引用附件"),
            "wecom_app": ("企业微信应用", "AMR、WAV、MP3、Speex", "不提供独立的引用内容解析"),
            "feishu": ("飞书", "语音：OPUS，最多 30 MiB", "支持引用当前消息回复"),
            "dingtalk": ("钉钉", "最多 2 MiB，最长 60 秒", "引用附件不随引用读取"),
        }
        for channel_type, (name, media, quote) in cases.items():
            with self.subTest(channel_type=channel_type):
                doc = await build_channel_doc(channel_type, registry.message_capabilities(channel_type))(ctx)
                self.assertTrue(doc.startswith(f"# {name}（{channel_type}）"))
                self.assertIn(media, doc)
                self.assertIn(quote, doc)
                self.assertIn("/new", doc)
                self.assertIn("合并转发不会展开", doc)
                self.assertIn("附件合计最多 50 MiB", doc)
                self.assertIn("公开链接取件还受 20 MiB", doc)
                self.assertEqual("群内成员执行" in doc, channel_type != "wecom_app")
                prompt = channel_instructions(channel_type)
                self.assertIn(name, prompt)
                self.assertIn("channel.message_source", prompt)
                self.assertIn("channel.message_quote", prompt)
                self.assertNotIn("/new", prompt)
                for other_type, (other_name, _, _) in cases.items():
                    if other_type != channel_type:
                        self.assertNotIn(other_name, doc)
                        self.assertNotIn(other_name, prompt)

        settings.get.return_value = SimpleNamespace(version=99, value={})
        doc = await build_channel_doc("feishu", registry.message_capabilities("feishu"))(ctx)
        self.assertIn("暂时无法读取服务端附件配置", doc)
        self.assertIn("/new", doc)

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
