from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage

from deep_agent.agent.context.compaction import DeepAgentContextCompaction
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.spec import AgentSpec
from deep_agent.api.v1.deps import _build_runner_from_spec
from deep_agent.capabilities import build_registry
from deep_agent.schemas.attachment import AttachmentRef


class ReaderRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_assembled_readers_feed_followup_requests_and_existing_compaction(self):
        # Both readers use the production assembler. Only external network/LLM
        # responses are controlled; actual tool execution, spill filtering and
        # automatic context compaction run together.
        for window, ceiling, iterations in ((128_000, 24_000, 4), (256_000, 48_000, 1)):
            with self.subTest(window=window):
                body = "正文" * 18_000
                calls, observed = [], []

                def respond(messages, info):
                    if not calls:
                        # The Run wrapper normally supplies persisted raw user
                        # events. Retain that authoritative input in this
                        # in-process agent fixture as well.
                        deps.context_state.raw_history = list(messages)
                    returns = [part for message in messages if isinstance(message, ModelRequest)
                               for part in message.parts if isinstance(part, ToolReturnPart)
                               and part.tool_name in {"read_file", "read_tool_result"}]
                    if calls:
                        latest = {part.tool_name: part for part in returns[-2:]}
                        if latest:
                            self.assertEqual(set(latest), {"read_file", "read_tool_result"})
                        else:
                            # A completed pair of large tool results can be
                            # summarized by the existing immutable-unit policy.
                            self.assertIn("已读取两个文档", str(messages))
                            self.assertIn("Read both authorized documents.", str(messages))
                        for part in latest.values():
                            value = part.content.model_dump(mode="json") if hasattr(part.content, "model_dump") else part.content
                            self.assertIsInstance(value, dict)
                            self.assertLessEqual(runner._estimator.text(value), ceiling)
                            self.assertLessEqual(runner._estimator.text(part.model_response_str()), ceiling)
                            self.assertTrue(body.startswith(value["content"]))
                            self.assertNotIn("overflow_handle", part.metadata or {})
                        observed.append(latest)
                    if len(calls) >= iterations:
                        return ModelResponse(parts=[TextPart("读取完成")], usage=RequestUsage(
                            input_tokens=runner._estimator.messages(messages), output_tokens=16))
                    calls.append(len(calls))
                    return ModelResponse(parts=[
                        ToolCallPart("read_file", {"resource": {"type": "attachment", "file_id": "document"}},
                                     f"file-{len(calls)}"),
                        ToolCallPart("read_tool_result", {"handle": "stored"}, f"stored-{len(calls)}"),
                    ], usage=RequestUsage(input_tokens=runner._estimator.messages(messages), output_tokens=128))

                spec = AgentSpec(name="reader-runtime", model="test", instructions="Read both files.",
                                 context_window_tokens=window, max_output_tokens=10_000)
                ctx = SimpleNamespace(stores=MagicMock(), registry=build_registry(), store=MagicMock(),
                                      skill_runtime=MagicMock(), sandbox_backend=None)
                with (
                    patch("deep_agent.api.v1.deps.load_mcp_rows", AsyncMock(return_value=[])),
                    patch("deep_agent.api.v1.deps.prepare_ui_tool_packages_for_spec", AsyncMock(return_value=([], {}))),
                    patch("deep_agent.api.v1.deps.resolve_mcp_toolsets", AsyncMock(return_value=[])),
                    patch("deep_agent.api.v1.deps.build_mcp_resource_toolset", return_value=None),
                    patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)),
                ):
                    runner = await _build_runner_from_spec(spec, ctx)
                deps = AgentDeps(session_id="reader-runtime", attachment_by_id={
                    "document": AttachmentRef(id="document", name="document.txt", mime="text/plain"),
                })
                runner._tool_output_store.bind(deps, [ModelRequest(parts=[ToolReturnPart(
                    "original_tool", "preview", "original",
                    metadata={"overflow_handle": "stored", "overflow_bytes": len(body.encode())},
                )])])
                deps.file_bytes["tool-result:stored"] = body.encode()
                download = AsyncMock(return_value=body.encode())
                summary = AsyncMock(return_value="已读取两个文档，继续完成读取验证。")
                with (
                    patch("deep_agent.files.toolset._download_file", download),
                    patch.object(DeepAgentContextCompaction, "_single_summary", summary),
                ):
                    result = await runner.agent.run("Read both authorized documents.", deps=deps)
                self.assertEqual(result.output, "读取完成")
                self.assertEqual(len(observed), iterations)
                download.assert_awaited_once()
                self.assertEqual(runner._tool_output_store.telemetry.spill_count, 0)
                self.assertEqual(runner._tool_output_store.telemetry.platform_downloads, 0)
                self.assertEqual(runner._tool_output_store.telemetry.read_calls, iterations)
                if iterations > 1:
                    self.assertGreater(summary.await_count, 0)
                    self.assertTrue(deps.context_state.telemetry.summary_applied)
                else:
                    summary.assert_not_awaited()
                    self.assertEqual(observed[0]["read_file"].content.content, body)
                    self.assertEqual(observed[0]["read_tool_result"].content["content"], body)
