"""Search configuration failures stay paired, persisted tool results, not Run errors."""
from __future__ import annotations

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent
from pydantic_ai.messages import TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from deep_agent.conversation import project_session_events
from deep_agent.web.runtime import build_web_runtime
from deep_agent.web.settings import WEB_SEARCH_SCHEMA_VERSION, WEB_SEARCH_SETTING_KEY, load_web_search_config
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


class WebSearchFailureEventsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stores, self.engine, _ = await make_sqlite_stores()
        self.store = self.stores.coordinator
        self.agent_id = await seed_default_agent(self.stores, name="isolated-search-failures")
        self.persister = EventPersister(self.store)
        await self.persister.start()

    async def asyncTearDown(self) -> None:
        await self.persister.aclose()
        await self.engine.dispose()

    async def _assert_failure_continues(self, stored_config: dict | None, expected_reason: str) -> None:
        if stored_config is not None:
            await self.stores.global_setting.upsert(
                WEB_SEARCH_SETTING_KEY, value=stored_config, version=WEB_SEARCH_SCHEMA_VERSION,
            )
        config = await load_web_search_config(
            self.stores.global_setting, self.stores.llm_configuration,
        )
        runtime = build_web_runtime(config)
        model_requests = 0
        seen_reason = []
        query = "latest Python release"
        final_answer = "网络搜索工具调用失败：请检查全局搜索供应商配置。"

        async def stream(messages, info):
            nonlocal model_requests
            model_requests += 1
            if model_requests == 1:
                self.assertIn("web_search", [tool.name for tool in info.function_tools])
                yield {0: DeltaToolCall(
                    name="web_search", json_args=json.dumps({"query": query}),
                    tool_call_id="failed-search-1",
                )}
                return
            self.assertEqual(model_requests, 2, "A failed result must not trigger automatic tool retries")
            results = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart)]
            self.assertEqual(len(results), 1)
            self.assertEqual(results[0].outcome, "failed")
            self.assertIn(expected_reason, str(results[0].content))
            seen_reason.append(results[0].content)
            yield final_answer

        runner = AgentRunner(
            Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps, toolsets=[runtime.toolset]),
            self.store, UsageLimits(request_limit=3),
        )
        session = "search-failure"
        deps = AgentDeps(session_id=session, user="isolated-search-user", agent_id=self.agent_id)
        with patch.object(AgentRunner, "_generate_model_title", new=AsyncMock()):
            async with asyncio.timeout(5):
                events = [event async for event in runner.run_stream(
                    session, "请帮我搜索最新 Python 版本", deps, persister=self.persister,
                )]
        self.assertEqual(model_requests, 2)
        self.assertEqual(len(seen_reason), 1)
        event_types = [event["type"] for event in events]
        self.assertNotIn("error", event_types)
        self.assertNotIn("tool_call_removed", event_types)
        self.assertEqual(events[-1]["type"], "final")
        self.assertEqual(events[-1]["status"], "completed")
        calls = [event for event in events if event["type"] == "tool_call"]
        results = [event for event in events if event["type"] == "tool_result"]
        self.assertEqual(len(calls), 1)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["outcome"], "failed")
        self.assertIn(expected_reason, str(results[0]))
        self.assertLess(event_types.index("tool_call"), event_types.index("tool_result"))
        self.assertLess(event_types.index("tool_result"), event_types.index("final"))

        await self.persister.flush(session, agent_id=self.agent_id)
        history = await self.store.get_raw_history(session, agent_id=self.agent_id)
        parts = [part for message in history for part in message.parts]
        stored_calls = [part for part in parts if isinstance(part, ToolCallPart)]
        stored_results = [part for part in parts if isinstance(part, ToolReturnPart)]
        self.assertEqual([part.tool_name for part in stored_calls], ["web_search"])
        self.assertEqual([part.tool_call_id for part in stored_calls], ["failed-search-1"])
        self.assertEqual([part.tool_call_id for part in stored_results], ["failed-search-1"])
        self.assertEqual(stored_results[0].outcome, "failed")
        self.assertIn(expected_reason, str(stored_results[0].content))
        self.assertIn(final_answer, [part.content for part in parts if isinstance(part, TextPart)])

        persisted_events = await self.store.list_all_events(session)
        self.assertFalse(any(event.event_type == "execution/error" for event in persisted_events))
        projected = project_session_events(persisted_events, session_id=session)
        tools = [item for item in projected.items if item.kind == "tool"]
        self.assertEqual(len(tools), 1)
        self.assertEqual(tools[0].call_id, "failed-search-1")
        self.assertEqual(tools[0].tool_name, "web_search")
        self.assertEqual(json.loads(tools[0].input), {"query": query})
        self.assertEqual(tools[0].status, "failed")
        self.assertEqual(tools[0].outcome, "failed")
        self.assertIn(expected_reason, str(tools[0].output))
        self.assertIn(final_answer, [item.text for item in projected.items if item.kind == "assistant_message"])
        # Reading a legacy/invalid setting must neither migrate nor create it.
        row = await self.stores.global_setting.get(WEB_SEARCH_SETTING_KEY)
        self.assertEqual(row.value if row else None, stored_config)

    async def test_empty_configuration_persists_failure_and_allows_final_answer(self) -> None:
        await self._assert_failure_continues(None, "未配置具体的搜索供应商")

    async def test_legacy_native_configuration_persists_failure_without_rewriting_setting(self) -> None:
        await self._assert_failure_continues({"mode": "native"}, "未配置具体的搜索供应商")

    async def test_missing_provider_reference_persists_failure_and_allows_final_answer(self) -> None:
        await self._assert_failure_continues({
            "mode": "provider", "provider": "kimi", "credential_source": "llm_provider",
            "llm_provider_id": "deleted-search-provider", "model": "legacy-kimi-model",
        }, "Configured LLM provider no longer exists")
