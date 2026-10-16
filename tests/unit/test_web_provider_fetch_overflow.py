"""Provider Fetch keeps full Unicode evidence through the real overflow protocol."""
from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_output_limits import build_tool_output_limits
from deep_agent.web.runtime import _provider_web_fetch_tool
from deep_agent.web.settings import WebSearchRuntimeConfig


class ProviderFetchOverflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_both_providers_spill_once_and_resume_complete_chinese_emoji_body(self) -> None:
        body = "# 来源标题\n中文、数字 1,234.56，比例 12.5%。\n" + "网页正文😀🧭" * 17_001 + "末尾证据-END✅"
        url = "https://docs.deepfos.com/fixture-public-page"
        title = "完整网页证据😀"
        for provider in ("deepseek", "kimi"):
            for window in (None, 1_000_000):
                with self.subTest(provider=provider, context_window=window):
                    handle = f"{provider}-overflow-fixture"
                    upload = AsyncMock(return_value=SimpleNamespace(id=handle))
                    files = SimpleNamespace(upload=upload, download=AsyncMock())
                    capability, store = build_tool_output_limits(
                        request_headers={}, context_window_tokens=window,
                        max_output_tokens=8_000, estimator=TokenEstimator.conservative(),
                        file_store=files,
                    )
                    deps = AgentDeps(session_id=f"{provider}-overflow-{window}")
                    store.bind(deps, [])
                    chunks: list[str] = []
                    cursors: set[tuple[int, int]] = set()

                    def model(messages, info):
                        returned = [
                            part for message in messages if isinstance(message, ModelRequest)
                            for part in message.parts if isinstance(part, ToolReturnPart)
                        ]
                        if not returned:
                            return ModelResponse(parts=[ToolCallPart("web_fetch", {"url": url}, "fetch")])
                        latest = returned[-1]
                        if latest.tool_name == "web_fetch":
                            self.assertEqual(latest.metadata["overflow_handle"], handle)
                            self.assertLess(len(str(latest.content)), len(body))
                            self.assertEqual(latest.metadata["sources"], [{"url": url, "title": title}])
                            self.assertEqual(latest.metadata["web_fetch"]["provider"], provider)
                            self.assertEqual(latest.metadata["web_fetch"]["mode"], "provider")
                            self.assertFalse(latest.metadata["web_fetch"]["truncated"])
                            self.assertTrue(latest.metadata["web_content_untrusted"])
                            return ModelResponse(parts=[ToolCallPart(
                                "read_tool_result", {"handle": handle, "pattern": '"content":'}, "read-0",
                            )])
                        page = latest.content
                        self.assertNotIn("error", page)
                        self.assertEqual(page["handle"], handle)
                        self.assertNotIn("\ufffd", page["content"])
                        cursor = (page["offset"], page["char_offset"])
                        self.assertNotIn(cursor, cursors)
                        cursors.add(cursor)
                        chunks.append(page["content"])
                        if page["next_read"] is not None:
                            self.assertTrue(page["has_more"])
                            self.assertEqual(page["next_read"]["handle"], handle)
                            return ModelResponse(parts=[ToolCallPart(
                                "read_tool_result", page["next_read"], f"read-{len(chunks)}",
                            )])
                        self.assertFalse(page["has_more"])
                        reconstructed = json.loads("{" + "".join(chunks).strip().rstrip(",") + "}")
                        self.assertEqual(reconstructed["content"], body)
                        return ModelResponse(parts=[TextPart("complete")])

                    config = WebSearchRuntimeConfig(
                        mode="provider", provider=provider, api_key="fixture-api-key",
                        fetch_mode="follow_search",
                    )
                    agent = Agent(
                        FunctionModel(model), deps_type=AgentDeps,
                        tools=[_provider_web_fetch_tool(config)], capabilities=[capability],
                    )
                    with patch("deep_agent.web.runtime.validate_and_resolve_url", new=AsyncMock()) as validate, \
                         patch(f"deep_agent.web.runtime.execute_{provider}_fetch", new=AsyncMock(
                             return_value={"title": title, "content": body},
                         )) as fetch:
                        result = await agent.run("Read the entire public page", deps=deps)
                    self.assertEqual(result.output, "complete")
                    self.assertGreater(len(chunks), 1)
                    self.assertTrue(any(char_offset > 0 for _, char_offset in cursors))
                    validate.assert_awaited_once_with(url, allow_local=False)
                    fetch.assert_awaited_once_with(api_key="fixture-api-key", base_url=None, url=url)
                    upload.assert_awaited_once()
                    stored = json.loads(upload.call_args.kwargs["data"])
                    self.assertEqual(stored["content"], body)
                    self.assertEqual(stored["title"], title)
                    self.assertEqual(stored["url"], url)
                    self.assertEqual(stored["provider"], provider)
                    self.assertTrue(stored["untrusted_external_content"])
                    self.assertEqual(store.telemetry.spill_count, 1)
                    self.assertEqual(store.telemetry.read_calls, len(chunks))
                    files.download.assert_not_awaited()
