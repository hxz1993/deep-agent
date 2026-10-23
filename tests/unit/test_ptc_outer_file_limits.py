from __future__ import annotations

import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock

from pydantic_ai import RunContext, ToolReturn
from pydantic_ai.messages import ModelRequest, ToolCallPart, ToolReturnPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.attachments import _looks_like_platform_error_json
from deep_agent.agent.tool_output_limits import build_tool_output_limits, session_file_metadata_from_history
from deep_agent.files.storage import StoredFile
from deep_agent.ptc.diagnostics import Collector, current_collector


class _MemoryFileStore:
    def __init__(self):
        self.files = {}

    async def upload(self, **kwargs):
        self.files["full-file-list"] = kwargs["data"]
        return StoredFile(id="full-file-list", file_name=kwargs["file_name"],
                          file_size=len(kwargs["data"]), file_type=kwargs["file_type"],
                          mime=kwargs.get("mime"))

    async def download(self, file_id, request_headers):
        return self.files[file_id]


class PlatformErrorPrefixTests(unittest.TestCase):
    def test_utf8_prefix_cut_inside_character_is_not_a_platform_error_packet(self):
        data = b'{"text":"' + ("中" * 2_000).encode("utf-8") + b'"}'
        self.assertEqual(json.loads(data)["text"], "中" * 2_000)
        with self.assertRaises(UnicodeDecodeError):
            json.loads(data[:4_096])
        self.assertFalse(_looks_like_platform_error_json(data))

    def test_complete_platform_error_packet_keeps_existing_detection(self):
        error = json.dumps({"status": False, "message": "权限不足", "code": "403"},
                           ensure_ascii=False).encode("utf-8")
        self.assertTrue(_looks_like_platform_error_json(error))
        self.assertFalse(_looks_like_platform_error_json(b'{"status":true}'))


class PtcOuterFileBudgetTests(unittest.IsolatedAsyncioTestCase):
    def _setup(self, *, real_outer=True, file_store=None):
        deps = AgentDeps(session_id="synthetic-file-budget", run_id="synthetic-run")
        if real_outer:
            deps.ptc_diagnostics["outer"] = {"version": 1, "call_id": "outer"}
        estimator = TokenEstimator.conservative()
        limits, store = build_tool_output_limits(
            request_headers={}, context_window_tokens=1_000_000, max_output_tokens=8_000,
            estimator=estimator, file_store=file_store or _MemoryFileStore(),
        )
        store.bind(deps, [])
        store.write = AsyncMock(wraps=store.write)
        ctx = RunContext(deps=deps, model=TestModel(), usage=RunUsage(), run_id="synthetic-run")
        return deps, ctx, limits, store

    @staticmethod
    def _files(count):
        return [{"file_id": f"file-{index:04}", "file_name": "财务报告" * 45 + ".html",
                 "mime": "text/html", "byte_size": 1_000, "sha256": "a" * 64}
                for index in range(count)]

    async def _reduce(self, ctx, limits, value):
        return await limits.after_tool_execute(
            ctx, call=ToolCallPart("run_code", {}, "outer"),
            tool_def=ToolDefinition(name="run_code"), args={}, result=value,
        )

    async def test_many_real_outer_file_refs_are_limited_before_model_delivery(self):
        _deps, ctx, limits, store = self._setup()
        refs = self._files(1_000)
        result = await self._reduce(ctx, limits, ToolReturn(
            return_value={"ok": True}, metadata={"code_mode": True, "generated_files": refs},
        ))
        store.write.assert_awaited_once()
        self.assertEqual(result.metadata["overflow_handle"], "full-file-list")
        self.assertIsInstance(result.return_value, str)
        self.assertIn("read_tool_result", result.return_value)
        stored = json.loads(store.write.await_args.args[1])
        self.assertTrue(stored["ok"])
        self.assertEqual(len(stored["files"]), 1_000)
        self.assertEqual(stored["files"][-1]["file_id"], refs[-1]["file_id"])
        self.assertEqual(len(result.metadata["generated_files"]), 1_000)
        history = [ModelRequest(parts=[ToolReturnPart(
            "run_code", result.return_value, "outer", metadata=result.metadata,
        )])]
        restored = session_file_metadata_from_history(history)
        self.assertEqual(len(restored), 1_001)  # all genuine files plus the result artifact
        self.assertEqual(restored[refs[-1]["file_id"]]["sha256"], refs[-1]["sha256"])
        next_deps, _ctx, _limits, next_store = self._setup(file_store=store._file_store)
        next_store.bind(next_deps, history)
        self.assertIn(refs[-1]["file_id"], next_deps.session_file_artifacts)
        restored_value = json.loads(await next_store.read("full-file-list"))
        self.assertEqual(len(restored_value["files"]), 1_000)
        self.assertEqual(restored_value["files"][-1]["file_id"], refs[-1]["file_id"])

    async def test_small_real_outer_file_ref_remains_visible(self):
        _deps, ctx, limits, store = self._setup()
        refs = self._files(1)
        result = await self._reduce(ctx, limits, ToolReturn(
            return_value={"ok": True}, metadata={"code_mode": True, "generated_files": refs},
        ))
        store.write.assert_not_awaited()
        self.assertEqual(result.return_value["files"][0]["file_id"], refs[0]["file_id"])
        self.assertEqual(len(result.metadata["generated_files"]), 1)

    async def test_metadata_without_execution_fact_keeps_existing_tool_semantics(self):
        _deps, ctx, limits, store = self._setup(real_outer=False)
        refs = self._files(1_000)
        result = await self._reduce(ctx, limits, ToolReturn(
            return_value={"ok": True}, metadata={"code_mode": True, "generated_files": refs},
        ))
        store.write.assert_not_awaited()
        self.assertEqual(len(result.return_value["files"]), 1_000)

    async def test_registered_inner_function_still_bypasses_model_budget(self):
        deps, ctx, limits, store = self._setup()
        call = ToolCallPart("execute_python", {}, "child")
        collector = Collector(parent_id="outer", run_id=deps.run_id,
                              session_id=deps.session_id, sensitive_values=frozenset())
        collector.execution = SimpleNamespace(nested_calls={"child": call})
        value = ToolReturn(return_value={"text": "大" * 100_000}, metadata={"generated_files": self._files(1_000)})
        token = current_collector.set(collector)
        try:
            result = await limits.after_tool_execute(ctx, call=call,
                tool_def=ToolDefinition(name="execute_python"), args={}, result=value)
        finally:
            current_collector.reset(token)
        self.assertIs(result, value)
        store.write.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
