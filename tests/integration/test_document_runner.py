"""Production assembler + SDK execution, with controlled external responses."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import RequestUsage

from deep_agent.agent.context.compaction import DeepAgentContextCompaction
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.spec import AgentSpec
from deep_agent.api.v1.deps import _build_runner_from_spec
from deep_agent.api.v1.document_parsing import connection_sample
from deep_agent.capabilities import build_registry
from deep_agent.documents.models import DocumentPage, ParsedDocument
from deep_agent.schemas.attachment import AttachmentRef

RESOURCE = {"type": "attachment", "file_id": "pdf"}


class DocumentRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def assemble(self, model, provider="glm", window=128000):
        self.store = SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(version=1,
            value={"mode": "provider", "provider": provider, "api_key": "controlled-key"})))
        ctx = SimpleNamespace(stores=MagicMock(global_setting=self.store), registry=build_registry(), store=MagicMock(),
                              skill_runtime=MagicMock(), sandbox_backend=None)
        ctx.stores.mcp.list_all = AsyncMock(return_value=[])
        ctx.stores.ui_tool_package.list_all = AsyncMock(return_value=[])
        ctx.stores.ui_tool.list_all = AsyncMock(return_value=[])
        spec = AgentSpec(name="document-runner", model="test", capabilities=["builtin:document"],
                         context_window_tokens=window, max_output_tokens=10000, instructions="Read authorized documents.")
        with (
            patch("deep_agent.api.v1.deps.load_mcp_rows", AsyncMock(return_value=[])),
            patch("deep_agent.api.v1.deps.prepare_ui_tool_packages_for_spec", AsyncMock(return_value=([], {}))),
            patch("deep_agent.api.v1.deps.resolve_mcp_toolsets", AsyncMock(return_value=[])),
            patch("deep_agent.api.v1.deps.build_mcp_resource_toolset", return_value=None),
            patch("deep_agent.agent.factory._resolve_model", return_value=model),
        ):
            return await _build_runner_from_spec(spec, ctx)

    def dependencies(self):
        return AgentDeps(session_id="document-integration", attachment_by_id={"pdf": AttachmentRef(id="pdf", name="sample.pdf", mime="application/pdf")})

    def response(self, messages, info):
        if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
            return ModelResponse(parts=[TextPart("read complete")])
        return ModelResponse(parts=[ToolCallPart("parse_document", {"resource": RESOURCE}, "read-doc")])

    async def test_configuration_snapshot_next_assembly_and_model_failure_cleanup(self):
        providers, directories = [], []
        async def native(config, source, cache_dir, **kwargs):
            providers.append(config.provider)
            directories.append(cache_dir.parent)
            (cache_dir / "raw.json").write_text("controlled original")
            return ParsedDocument(config.provider, config.model, [DocumentPage(1, "123.45")], 1)
        runner = await self.assemble(FunctionModel(self.response))
        self.store.get.return_value.value = {"mode": "provider", "provider": "paddle", "api_key": "new-key"}
        with patch("deep_agent.documents.toolset.parse_with_provider", side_effect=native), patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=connection_sample().data)):
            deps = self.dependencies()
            self.assertEqual((await runner.agent.run("read", deps=deps)).output, "read complete")
            self.assertIsNone(deps.document_cache)
            self.assertFalse(directories[-1].exists())
            next_runner = await self.assemble(FunctionModel(self.response), provider="paddle")
            await next_runner.agent.run("read", deps=self.dependencies())
            self.assertEqual(providers, ["glm", "paddle"])
            def broken(messages, info):
                response = self.response(messages, info)
                if isinstance(response.parts[0], TextPart):
                    raise RuntimeError("controlled model failure")
                return response
            failed = await self.assemble(FunctionModel(broken), provider="glm")
            deps = self.dependencies()
            with self.assertRaises(RuntimeError):
                await failed.agent.run("read", deps=deps)
            self.assertIsNone(deps.document_cache)
            self.assertTrue(all(not directory.exists() for directory in directories))

    async def test_actual_stream_success_and_cancel_release_cache(self):
        directories = []
        ready = asyncio.Event()
        cancelled = asyncio.Event()
        block = False
        async def native(config, source, cache_dir, **kwargs):
            directories.append(cache_dir.parent)
            (cache_dir / "raw.json").write_text("controlled original")
            if block:
                ready.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    cancelled.set()
            return ParsedDocument(config.provider, config.model, [DocumentPage(1, "123.45")], 1)
        async def stream(messages, info):
            if any(isinstance(part, ToolReturnPart) for message in messages for part in message.parts):
                yield "stream complete"
            else:
                yield {0: DeltaToolCall(name="parse_document", json_args=json.dumps({"resource": RESOURCE}), tool_call_id="stream-doc")}
        runner = await self.assemble(FunctionModel(stream_function=stream))
        async def execute(deps):
            async with runner.agent.run_stream_events("read", deps=deps) as events:
                async for _ in events:
                    pass
        with patch("deep_agent.documents.toolset.parse_with_provider", side_effect=native), patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=connection_sample().data)):
            deps = self.dependencies()
            await execute(deps)
            self.assertIsNone(deps.document_cache)
            self.assertFalse(directories[-1].exists())
            block = True
            deps = self.dependencies()
            task = asyncio.create_task(execute(deps))
            await asyncio.wait_for(ready.wait(), 5)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertTrue(cancelled.is_set())
            self.assertIsNone(deps.document_cache)
            self.assertTrue(all(not directory.exists() for directory in directories))

    async def test_assembled_document_result_participates_existing_compaction(self):
        calls = []
        deps = self.dependencies()
        def respond(messages, info):
            if not calls:
                deps.context_state.raw_history = list(messages)
            calls.append(len(calls))
            if len(calls) > 5:
                return ModelResponse(parts=[TextPart("complete")], usage=RequestUsage(input_tokens=runner._estimator.messages(messages), output_tokens=16))
            return ModelResponse(parts=[ToolCallPart("parse_document", {"resource": RESOURCE}, f"read-{len(calls)}")],
                                 usage=RequestUsage(input_tokens=runner._estimator.messages(messages), output_tokens=128))
        runner = await self.assemble(FunctionModel(respond))
        summary = AsyncMock(return_value="已读取文档，继续执行后续读取验证。")
        original = ParsedDocument("paddle", "PaddleOCR-VL-1.6", [DocumentPage(1, "中文" * 22000)], 1)
        with (patch("deep_agent.documents.toolset.parse_with_provider", AsyncMock(return_value=original)) as parser,
             patch("deep_agent.files.toolset._download_file", AsyncMock(return_value=connection_sample().data)),
             patch.object(DeepAgentContextCompaction, "_single_summary", summary)):
            output = await runner.agent.run("Read the authorized document.", deps=deps)
        self.assertEqual(output.output, "complete")
        parser.assert_awaited_once()
        self.assertEqual(runner._tool_output_store.telemetry.spill_count, 0)
        self.assertGreater(summary.await_count, 0)
        self.assertTrue(deps.context_state.telemetry.summary_applied)
        self.assertIsNone(deps.document_cache)
