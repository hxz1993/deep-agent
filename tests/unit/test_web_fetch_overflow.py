from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx2
from pydantic_ai import Agent, ModelRetry, ToolFailed
from pydantic_ai._ssrf import _read_capped_body
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_output_limits import build_tool_output_limits
from deep_agent.web.runtime import _web_fetch_tool


class WebFetchOverflowTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_page_spills_complete_body_and_model_can_resume_past_old_cutoff(self) -> None:
        body = "网页正文" * 15_001 + "末尾证据-END"
        url = "https://example.com/long-page"
        response = httpx2.Response(
            200, headers={"content-type": "text/plain; charset=utf-8"},
            content=body.encode(), request=httpx2.Request("GET", url),
        )
        upload = AsyncMock(return_value=SimpleNamespace(id="web-overflow-fixture"))
        files = SimpleNamespace(upload=upload, download=AsyncMock())
        capability, store = build_tool_output_limits(
            request_headers={}, context_window_tokens=1_000_000,
            max_output_tokens=8_000, estimator=TokenEstimator.conservative(),
            file_store=files,
        )
        deps = AgentDeps(session_id="web-overflow-test")
        store.bind(deps, [])
        chunks: list[str] = []

        def model(messages, info):
            returned = [
                part for message in messages if isinstance(message, ModelRequest)
                for part in message.parts if isinstance(part, ToolReturnPart)
            ]
            if not returned:
                return ModelResponse(parts=[ToolCallPart("web_fetch", {"url": url}, "fetch")])
            latest = returned[-1]
            if latest.tool_name == "web_fetch":
                self.assertEqual(latest.metadata["overflow_handle"], "web-overflow-fixture")
                self.assertLess(len(str(latest.content)), len(body))
                self.assertFalse(latest.metadata["web_fetch"]["truncated"])
                self.assertTrue(latest.metadata["web_content_untrusted"])
                return ModelResponse(parts=[ToolCallPart(
                    "read_tool_result", {"handle": "web-overflow-fixture", "pattern": '"content":'}, "read-0",
                )])
            page = latest.content
            chunks.append(page["content"])
            self.assertLessEqual(len(page["content"]), 50_000)
            if page["next_read"] is not None:
                return ModelResponse(parts=[ToolCallPart(
                    "read_tool_result", page["next_read"], f"read-{len(chunks)}",
                )])
            reconstructed = json.loads("{" + "".join(chunks).strip().rstrip(",") + "}")
            self.assertEqual(reconstructed["content"], body)
            return ModelResponse(parts=[TextPart("complete")])

        agent = Agent(
            FunctionModel(model), deps_type=AgentDeps,
            tools=[_web_fetch_tool()], capabilities=[capability],
        )
        with patch("deep_agent.web.runtime.safe_download", AsyncMock(return_value=response)) as download:
            result = await agent.run("Read the complete page", deps=deps)
        self.assertEqual(result.output, "complete")
        self.assertGreater(len(chunks), 1)
        upload.assert_awaited_once()
        self.assertEqual(json.loads(upload.call_args.kwargs["data"])["content"], body)
        files.download.assert_not_awaited()
        self.assertEqual(download.call_args.kwargs["max_bytes"], 5 * 1024 * 1024)
        self.assertFalse(download.call_args.kwargs["allow_local"])

    async def test_small_page_remains_inline_with_source_and_untrusted_marker(self) -> None:
        url = "https://example.com/small"
        response = httpx2.Response(
            200, headers={"content-type": "text/plain"}, content=b"small body",
            request=httpx2.Request("GET", url),
        )
        upload = AsyncMock()
        capability, store = build_tool_output_limits(
            request_headers={}, context_window_tokens=1_000_000,
            max_output_tokens=8_000, estimator=TokenEstimator.conservative(),
            file_store=SimpleNamespace(upload=upload),
        )
        deps = AgentDeps(session_id="small-web-test")
        store.bind(deps, [])

        def model(messages, info):
            if len(messages) == 1:
                return ModelResponse(parts=[ToolCallPart("web_fetch", {"url": url}, "fetch")])
            result = next(p for p in messages[-1].parts if isinstance(p, ToolReturnPart))
            self.assertEqual(result.content["content"], "small body")
            self.assertTrue(result.content["untrusted_external_content"])
            self.assertEqual(result.metadata["sources"][0]["url"], url)
            self.assertNotIn("overflow_handle", result.metadata)
            return ModelResponse(parts=[TextPart("complete")])

        agent = Agent(FunctionModel(model), deps_type=AgentDeps, tools=[_web_fetch_tool()], capabilities=[capability])
        with patch("deep_agent.web.runtime.safe_download", AsyncMock(return_value=response)):
            await agent.run("Read small page", deps=deps)
        upload.assert_not_awaited()

    async def test_download_safety_failure_is_not_retried_or_converted_to_content(self) -> None:
        response = httpx2.Response(200, content=b"x" * (5 * 1024 * 1024 + 1))

        async def capped_download(url, **kwargs):
            # Exercise the installed SDK's actual byte limiter and exception
            # text, instead of inventing an application error contract.
            return await _read_capped_body(response, kwargs["max_bytes"])

        with patch(
            "deep_agent.web.runtime.safe_download",
            AsyncMock(side_effect=capped_download),
        ) as download:
            with self.assertRaisesRegex(ToolFailed, "maximum size"):
                await _web_fetch_tool().function("https://example.com/too-large")
        download.assert_awaited_once()
        self.assertEqual(download.call_args.kwargs["max_bytes"], 5 * 1024 * 1024)

    async def test_unrelated_download_value_error_remains_correctable(self) -> None:
        with patch(
            "deep_agent.web.runtime.safe_download",
            AsyncMock(side_effect=ValueError("URL resolves to a private/internal address")),
        ) as download:
            with self.assertRaisesRegex(ModelRetry, "private/internal"):
                await _web_fetch_tool().function("https://example.com/private")
        download.assert_awaited_once()
