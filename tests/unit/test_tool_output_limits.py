from __future__ import annotations

import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent, RunContext, ToolReturn
from pydantic_ai.messages import ImageUrl, ModelRequest, ToolCallPart, ToolReturnPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage

from deep_agent.agent.context.sanitization import serialize_sanitized_projection
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.context.file_keys import deep_agent_file_key
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.events import map_part
from deep_agent.agent.tool_output_limits import (
    PlatformToolResultStore,
    artifact_handles_from_history,
    artifact_metadata_from_history,
    build_tool_output_limits,
    session_file_metadata_from_history,
    tool_output_threshold_tokens,
)
from deep_agent.db.serialization import deserialize_message, serialize_message
from tests.helpers.conversation import project_model_messages


def _ctx(deps: AgentDeps | None = None) -> RunContext[AgentDeps]:
    return RunContext(
        deps=deps or AgentDeps(session_id="session"),
        model=TestModel(),
        usage=RunUsage(),
        run_id="run-1",
    )


class ToolOutputThresholdTests(unittest.TestCase):
    def test_threshold_is_dynamic_and_clamped(self) -> None:
        self.assertEqual(
            tool_output_threshold_tokens(context_window_tokens=None, max_output_tokens=None),
            24_000,
        )
        self.assertEqual(
            tool_output_threshold_tokens(context_window_tokens=10_000, max_output_tokens=2_000),
            4_000,
        )
        self.assertEqual(
            tool_output_threshold_tokens(context_window_tokens=100_000, max_output_tokens=8_000),
            18_400,
        )
        self.assertEqual(
            tool_output_threshold_tokens(context_window_tokens=1_000_000, max_output_tokens=8_000),
            48_000,
        )

    def test_document_limit_preserves_small_and_unknown_window_budgets(self) -> None:
        cases = [
            (None, 8_000, 24_000),
            (0, 8_000, 24_000),
            (-1, 8_000, 24_000),
            (128_000, 8_000, 24_000),
            (200_000, 8_000, 38_400),
            (248_000, 8_000, 48_000),
            (1_000_000, 384_000, 48_000),
            (10_000, 20_000, 4_000),
        ]
        for window, output, expected in cases:
            with self.subTest(window=window, output=output):
                self.assertEqual(
                    tool_output_threshold_tokens(
                        context_window_tokens=window, max_output_tokens=output,
                    ),
                    expected,
                )

    def test_read_tool_result_compaction_key_includes_slice(self) -> None:
        first = ToolCallPart(
            "read_tool_result",
            {"handle": "artifact-1", "offset": 0, "limit": 200},
        )
        same = ToolCallPart(
            "read_tool_result",
            {"handle": "artifact-1", "offset": 0, "limit": 200},
        )
        next_page = ToolCallPart(
            "read_tool_result",
            {"handle": "artifact-1", "offset": 200, "limit": 200},
        )
        self.assertEqual(deep_agent_file_key(first), deep_agent_file_key(same))
        self.assertNotEqual(deep_agent_file_key(first), deep_agent_file_key(next_page))


class PlatformToolResultStoreTests(unittest.IsolatedAsyncioTestCase):
    async def test_write_uses_platform_id_and_same_run_cache(self) -> None:
        deps = AgentDeps(session_id="session")
        store = PlatformToolResultStore(request_headers={"app": "app"}, threshold_tokens=4_000)
        store.bind(deps, [])
        upload = AsyncMock(return_value=SimpleNamespace(id="platform-file-1"))
        api = SimpleNamespace(file=SimpleNamespace(upload=upload))
        with patch("deep_agent.files.storage.PlatformAPI", return_value=api):
            handle = await store.write("run/call", b"complete output")
        self.assertEqual(handle, "platform-file-1")
        self.assertEqual(await store.read(handle), b"complete output")
        self.assertEqual(
            store.content_hash(handle),
            "8468c503ac8dae011ac26d9873b7198d2a64b8f1a3b38c0d5d8506482710bf03",
        )
        upload.assert_awaited_once()
        self.assertEqual(store.telemetry.platform_downloads, 0)
        self.assertEqual(store.telemetry.spill_count, 1)

    async def test_restart_scope_reads_authenticated_history_once(self) -> None:
        history = [
            ModelRequest(
                parts=[
                    ToolReturnPart(
                        "large_tool",
                        "preview",
                        "call-1",
                        metadata={"overflow_handle": "platform-file-1", "overflow_bytes": 50},
                    )
                ]
            )
        ]
        deps = AgentDeps(session_id="session")
        store = PlatformToolResultStore(request_headers={}, threshold_tokens=4_000)
        store.bind(deps, history)
        self.assertEqual(
            artifact_metadata_from_history(history)["platform-file-1"]["byte_size"],
            50,
        )
        self.assertEqual(deps.tool_output_artifacts["platform-file-1"]["role"], "return_value")
        self.assertEqual(
            deps.session_file_artifacts["platform-file-1"]["kind"],
            "tool_result_overflow",
        )
        download = AsyncMock(return_value=b"line 1\nline 2")
        with patch("deep_agent.agent.tool_output_limits._download_file", download):
            first, second = await asyncio.gather(
                store.read("platform-file-1"),
                store.read("platform-file-1"),
            )
        self.assertEqual(first, second)
        download.assert_awaited_once_with(
            "platform-file-1",
            {},
            file_store=store._file_store,
        )
        self.assertEqual(store.telemetry.platform_downloads, 1)

    async def test_generated_files_restore_separately_from_overflow_handles(self) -> None:
        generated = {
            "file_id": "generated-file-1",
            "file_name": "chart.png",
            "mime": "image/png",
            "byte_size": 321,
            "sha256": "a" * 64,
            "kind": "sandbox_output",
            "path": "/tmp/must-not-leak.png",
            "base64": "must-not-leak",
        }
        part = ToolReturnPart(
            "execute_python",
            {"result": {"ok": True}, "files": [generated]},
            "call-python",
            metadata={"generated_files": [generated]},
        )
        history = [ModelRequest(parts=[part])]
        self.assertEqual(artifact_metadata_from_history(history), {})
        restored = session_file_metadata_from_history(history)
        self.assertEqual(set(restored), {"generated-file-1"})
        self.assertEqual(restored["generated-file-1"]["file_name"], "chart.png")
        self.assertEqual(restored["generated-file-1"]["sha256"], "a" * 64)
        self.assertNotIn("path", restored["generated-file-1"])
        self.assertNotIn("base64", restored["generated-file-1"])

        deps = AgentDeps(session_id="session")
        store = PlatformToolResultStore(request_headers={}, threshold_tokens=4_000)
        store.bind(deps, history)
        self.assertEqual(deps.tool_output_artifacts, {})
        self.assertEqual(
            deps.session_file_artifacts["generated-file-1"]["kind"],
            "sandbox_output",
        )
        with self.assertRaises(FileNotFoundError):
            await store.read("generated-file-1")

        event = map_part(part)
        artifact = event["artifacts"][0]
        self.assertEqual(artifact["file_id"], "generated-file-1")
        self.assertEqual(artifact["mime"], "image/png")
        self.assertNotIn("path", artifact)
        self.assertNotIn("base64", artifact)
        projected = project_model_messages(history)
        projected_artifact = projected[0].artifacts[0]
        self.assertEqual(projected_artifact.file_id, "generated-file-1")
        self.assertEqual(projected_artifact.sha256, "a" * 64)

        serialized = serialize_message(ModelRequest(parts=[part]))
        restored_message = deserialize_message(serialized)
        self.assertEqual(
            session_file_metadata_from_history([restored_message])["generated-file-1"][
                "byte_size"
            ],
            321,
        )

    async def test_untrusted_handle_never_reaches_platform(self) -> None:
        deps = AgentDeps(session_id="session")
        store = PlatformToolResultStore(request_headers={}, threshold_tokens=4_000)
        store.bind(deps, [])
        download = AsyncMock()
        with patch("deep_agent.agent.tool_output_limits._download_file", download):
            with self.assertRaises(FileNotFoundError):
                await store.read("model-invented-id")
        download.assert_not_awaited()


class HarnessToolOutputLimitsTests(unittest.IsolatedAsyncioTestCase):
    async def test_long_document_keeps_structure_only_with_sufficient_model_window(self) -> None:
        original = {"path": "guide.md", "content": "文" * 30_000, "has_more": False}
        for window, should_spill in [(1_000_000, False), (128_000, True), (None, True)]:
            with self.subTest(window=window):
                capability, store = build_tool_output_limits(
                    request_headers={}, context_window_tokens=window,
                    max_output_tokens=8_000, estimator=TokenEstimator.conservative(),
                )
                store.write = AsyncMock(return_value="document-artifact")  # type: ignore[method-assign]
                result = await capability.after_tool_execute(
                    _ctx(), call=ToolCallPart("docs_read", {}, "call-document"),
                    tool_def=ToolDefinition(name="docs_read"), args={}, result=original,
                )
                if should_spill:
                    self.assertIsInstance(result, ToolReturn)
                    self.assertEqual(result.metadata["overflow_handle"], "document-artifact")
                    store.write.assert_awaited_once()
                    self.assertEqual(json.loads(store.write.await_args.args[1]), original)
                else:
                    self.assertIs(result, original)
                    store.write.assert_not_awaited()

    async def test_large_window_spills_at_exact_token_threshold(self) -> None:
        estimator = TokenEstimator.conservative()
        for size in [47_999, 48_000, 48_001]:
            with self.subTest(tokens=size):
                capability, store = build_tool_output_limits(
                    request_headers={}, context_window_tokens=1_000_000,
                    max_output_tokens=8_000, estimator=estimator,
                )
                store.write = AsyncMock(return_value="boundary-artifact")  # type: ignore[method-assign]
                original = "文" * size
                self.assertEqual(estimator.text(original), size)
                result = await capability.after_tool_execute(
                    _ctx(), call=ToolCallPart("docs_read", {}, "call-boundary"),
                    tool_def=ToolDefinition(name="docs_read"), args={}, result=original,
                )
                if size >= 48_000:
                    self.assertIsInstance(result, ToolReturn)
                    store.write.assert_awaited_once()
                    self.assertEqual(store.write.await_args.args[1].decode("utf-8"), original)
                else:
                    self.assertIs(result, original)
                    store.write.assert_not_awaited()

    async def test_large_sequence_spills_as_pageable_json_lines(self) -> None:
        capability, store = build_tool_output_limits(
            request_headers={},
            context_window_tokens=10_000,
            max_output_tokens=2_000,
            estimator=TokenEstimator.conservative(),
        )
        deps = AgentDeps(session_id="session")
        store.bind(deps, [])
        captured: list[bytes] = []

        async def capture_write(_key: str, data: bytes) -> str:
            captured.append(data)
            return "platform-file-jsonl"

        store.write = capture_write  # type: ignore[method-assign]
        rows = [
            {"id": index, "name": "上海" if index == 2 else f"成员{index}"}
            for index in range(1_000)
        ]

        result = await capability.after_tool_execute(
            _ctx(deps),
            call=ToolCallPart("large_query", {}, "call-jsonl"),
            tool_def=ToolDefinition(name="large_query"),
            args={},
            result=rows,
        )

        self.assertIsInstance(result, ToolReturn)
        self.assertEqual(result.metadata["overflow_handle"], "platform-file-jsonl")
        self.assertEqual(len(captured), 1)
        lines = captured[0].decode("utf-8").splitlines()
        self.assertEqual(len(lines), len(rows))
        self.assertEqual(json.loads(lines[0]), rows[0])
        self.assertEqual(json.loads(lines[2]), rows[2])

    async def test_small_structured_result_keeps_original_object(self) -> None:
        capability, store = build_tool_output_limits(
            request_headers={},
            context_window_tokens=10_000,
            max_output_tokens=2_000,
            estimator=TokenEstimator.conservative(),
        )
        store.write = AsyncMock(return_value="unused")  # type: ignore[method-assign]
        original = [{"id": 1, "name": "上海"}]

        result = await capability.after_tool_execute(
            _ctx(),
            call=ToolCallPart("query", {}, "call-small-json"),
            tool_def=ToolDefinition(name="query"),
            args={},
            result=original,
        )

        self.assertIs(result, original)
        store.write.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_web_image_url_stays_native_and_never_creates_artifact(self) -> None:
        capability, store = build_tool_output_limits(
            request_headers={},
            context_window_tokens=10_000,
            max_output_tokens=2_000,
            estimator=TokenEstimator.conservative(),
        )
        store.write = AsyncMock(return_value="unused")  # type: ignore[method-assign]
        image = ImageUrl(
            url="https://cdn.example.com/chart.png",
            media_type="image/png",
            force_download=True,
        )

        result = await capability.after_tool_execute(
            _ctx(),
            call=ToolCallPart("web_image_view", {}, "image-1"),
            tool_def=ToolDefinition(name="web_image_view"),
            args={},
            result=image,
        )

        self.assertIs(result, image)
        store.write.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_generated_file_metadata_survives_large_result_spill(self) -> None:
        capability, store = build_tool_output_limits(
            request_headers={},
            context_window_tokens=10_000,
            max_output_tokens=2_000,
            estimator=TokenEstimator.conservative(),
        )
        store.write = AsyncMock(return_value="overflow-with-file")  # type: ignore[method-assign]
        generated = {
            "file_id": "generated-after-spill",
            "file_name": "report.pdf",
            "mime": "application/pdf",
            "byte_size": 2048,
            "sha256": "b" * 64,
            "kind": "sandbox_output",
        }
        original = ToolReturn(
            return_value={"result": "中文" * 20_000, "files": [generated]},
            metadata={"generated_files": [generated]},
        )

        result = await capability.after_tool_execute(
            _ctx(),
            call=ToolCallPart("execute_python", {}, "call-spill-file"),
            tool_def=ToolDefinition(name="execute_python"),
            args={},
            result=original,
        )

        self.assertIsInstance(result, ToolReturn)
        self.assertEqual(
            result.metadata["generated_files"][0]["file_id"],
            "generated-after-spill",
        )
        self.assertIsInstance(result.return_value, dict)
        self.assertEqual(
            result.return_value["files"][0]["file_id"],
            "generated-after-spill",
        )
        self.assertIn("read_tool_result", result.return_value["result"])
        part = ToolReturnPart(
            "execute_python",
            result.return_value,
            "call-spill-file",
            metadata=result.metadata,
        )
        kinds = {item["kind"] for item in map_part(part)["artifacts"]}
        self.assertEqual(kinds, {"tool_result_overflow", "sandbox_output"})

    async def test_capability_runs_inside_real_agent_loop(self) -> None:
        capability, store = build_tool_output_limits(
            request_headers={},
            context_window_tokens=10_000,
            max_output_tokens=2_000,
            estimator=TokenEstimator.conservative(),
        )
        deps = AgentDeps(session_id="session")
        store.bind(deps, [])
        store.write = AsyncMock(return_value="platform-file-agent")  # type: ignore[method-assign]

        agent = Agent(
            TestModel(call_tools=["large_query"], custom_output_text="done"),
            deps_type=AgentDeps,
            capabilities=[capability],
        )

        @agent.tool_plain
        def large_query() -> dict[str, object]:
            return {"rows": ["中文数据" * 5_000]}

        result = await agent.run("查询", deps=deps, run_id="run-1")
        returns = [
            part
            for message in result.new_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "large_query"
        ]
        self.assertEqual(len(returns), 1)
        self.assertEqual(returns[0].metadata["overflow_handle"], "platform-file-agent")
        self.assertIn("read_tool_result", returns[0].content)

    async def test_large_result_spills_and_projects_artifact(self) -> None:
        capability, store = build_tool_output_limits(
            request_headers={},
            context_window_tokens=10_000,
            max_output_tokens=2_000,
            estimator=TokenEstimator.conservative(),
        )
        deps = AgentDeps(session_id="session")
        store.bind(deps, [])
        store.write = AsyncMock(return_value="platform-file-1")  # type: ignore[method-assign]
        call = ToolCallPart("large_query", {"id": "1"}, "call-1")
        result = await capability.after_tool_execute(
            _ctx(deps),
            call=call,
            tool_def=ToolDefinition(name="large_query"),
            args={"id": "1"},
            result={"rows": ["中文数据" * 5_000]},
        )
        self.assertIsInstance(result, ToolReturn)
        self.assertIn("read_tool_result", result.return_value)
        self.assertEqual(result.metadata["overflow_handle"], "platform-file-1")
        self.assertEqual(store.write.await_count, 1)  # type: ignore[attr-defined]

        part = ToolReturnPart(
            "large_query",
            result.return_value,
            "call-1",
            metadata=result.metadata,
        )
        event = map_part(part)
        self.assertEqual(event["artifacts"][0]["artifact_id"], "platform-file-1")
        history = project_model_messages([ModelRequest(parts=[part])])
        self.assertEqual(history[0].artifacts[0].byte_size, result.metadata["overflow_bytes"])
        self.assertEqual(artifact_handles_from_history([ModelRequest(parts=[part])]), {"platform-file-1"})

        restored = deserialize_message(serialize_message(ModelRequest(parts=[part])))
        self.assertEqual(artifact_handles_from_history([restored]), {"platform-file-1"})
        restored_history = project_model_messages([restored])
        self.assertEqual(
            restored_history[0].artifacts[0].artifact_id,
            "platform-file-1",
        )

        sanitized = serialize_sanitized_projection([ModelRequest(parts=[part])])
        self.assertNotIn("overflow_handle", str(sanitized))
        self.assertLess(len(str(sanitized)), len(str({"rows": ["中文数据" * 5_000]})) // 2)

    async def test_small_and_excluded_results_pass_through(self) -> None:
        capability, store = build_tool_output_limits(
            request_headers={},
            context_window_tokens=10_000,
            max_output_tokens=2_000,
            estimator=TokenEstimator.conservative(),
        )
        store.write = AsyncMock(return_value="unused")  # type: ignore[method-assign]
        small = await capability.after_tool_execute(
            _ctx(),
            call=ToolCallPart("query", {}, "call-small"),
            tool_def=ToolDefinition(name="query"),
            args={},
            result={"ok": True},
        )
        excluded = await capability.after_tool_execute(
            _ctx(),
            call=ToolCallPart("read_file", {}, "call-file"),
            tool_def=ToolDefinition(name="read_file"),
            args={},
            result="中文" * 10_000,
        )
        self.assertEqual(small, {"ok": True})
        self.assertEqual(excluded, "中文" * 10_000)
        store.write.assert_not_awaited()  # type: ignore[attr-defined]

    async def test_upload_failure_uses_bounded_fallback(self) -> None:
        capability, store = build_tool_output_limits(
            request_headers={},
            context_window_tokens=10_000,
            max_output_tokens=2_000,
            estimator=TokenEstimator.conservative(),
        )
        upload = AsyncMock(side_effect=OSError("platform down"))
        api = SimpleNamespace(file=SimpleNamespace(upload=upload))
        original = "A" * 30_000
        with patch("deep_agent.files.storage.PlatformAPI", return_value=api):
            result = await capability.after_tool_execute(
                _ctx(),
                call=ToolCallPart("query", {}, "call-1"),
                tool_def=ToolDefinition(name="query"),
                args={},
                result=original,
            )
        self.assertIsInstance(result, ToolReturn)
        self.assertLessEqual(len(result.return_value), 8_200)
        self.assertTrue(result.metadata["spill_failed"])
        self.assertEqual(result.metadata["inline_fallback"], original)
        self.assertEqual(store.telemetry.spill_failures, 1)

    async def test_successful_spill_persists_full_artifact_hash(self) -> None:
        capability, store = build_tool_output_limits(
            request_headers={},
            context_window_tokens=10_000,
            max_output_tokens=2_000,
            estimator=TokenEstimator.conservative(),
        )
        deps = AgentDeps(session_id="session")
        store.bind(deps, [])
        upload = AsyncMock(return_value=SimpleNamespace(id="platform-file-hash"))
        api = SimpleNamespace(file=SimpleNamespace(upload=upload))
        with patch("deep_agent.files.storage.PlatformAPI", return_value=api):
            result = await capability.after_tool_execute(
                _ctx(deps),
                call=ToolCallPart("query", {}, "call-hash"),
                tool_def=ToolDefinition(name="query"),
                args={},
                result="完整结果" * 10_000,
            )
        self.assertIsInstance(result, ToolReturn)
        assert isinstance(result, ToolReturn)
        self.assertEqual(
            result.metadata["overflow_hash"],
            store.content_hash("platform-file-hash"),
        )
        self.assertEqual(len(result.metadata["overflow_hash"]), 64)

    async def test_return_value_and_model_content_spill_independently(self) -> None:
        capability, store = build_tool_output_limits(
            request_headers={},
            context_window_tokens=10_000,
            max_output_tokens=2_000,
            estimator=TokenEstimator.conservative(),
        )
        store.write = AsyncMock(  # type: ignore[method-assign]
            side_effect=["platform-value", "platform-content"]
        )
        result = await capability.after_tool_execute(
            _ctx(),
            call=ToolCallPart("query", {}, "call-1"),
            tool_def=ToolDefinition(name="query"),
            args={},
            result=ToolReturn(
                return_value="R" * 30_000,
                content="C" * 30_000,
            ),
        )
        self.assertIsInstance(result, ToolReturn)
        self.assertEqual(result.metadata["overflow_handle"], "platform-value")
        self.assertEqual(result.metadata["overflow_content_handle"], "platform-content")


if __name__ == "__main__":
    unittest.main()
