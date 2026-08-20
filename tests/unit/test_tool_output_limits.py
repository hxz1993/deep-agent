from __future__ import annotations

import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from pydantic_ai import Agent, RunContext, ToolReturn
from pydantic_ai.messages import ModelRequest, ToolCallPart, ToolReturnPart
from pydantic_ai.models.test import TestModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage

from deep_agent.agent.context.checkpoint import serialize_sanitized_projection
from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.context.file_keys import deep_agent_file_key
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.events import map_part
from deep_agent.agent.history import model_messages_to_history
from deep_agent.agent.tool_output_limits import (
    PlatformToolResultStore,
    artifact_handles_from_history,
    artifact_metadata_from_history,
    build_tool_output_limits,
    tool_output_threshold_tokens,
)
from deep_agent.db.serialization import deserialize_message, serialize_message


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
            24_000,
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
        with patch("deep_agent.agent.tool_output_limits.PlatformAPI", return_value=api):
            handle = await store.write("run/call", b"complete output")
        self.assertEqual(handle, "platform-file-1")
        self.assertEqual(await store.read(handle), b"complete output")
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
        download = AsyncMock(return_value=b"line 1\nline 2")
        with patch("deep_agent.agent.tool_output_limits._download_file", download):
            first, second = await asyncio.gather(
                store.read("platform-file-1"),
                store.read("platform-file-1"),
            )
        self.assertEqual(first, second)
        download.assert_awaited_once_with("platform-file-1", {})
        self.assertEqual(store.telemetry.platform_downloads, 1)

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
        history = model_messages_to_history([ModelRequest(parts=[part])])
        self.assertEqual(history[0].parts[0].artifacts[0]["byte_size"], result.metadata["overflow_bytes"])
        self.assertEqual(artifact_handles_from_history([ModelRequest(parts=[part])]), {"platform-file-1"})

        restored = deserialize_message(serialize_message(ModelRequest(parts=[part])))
        self.assertEqual(artifact_handles_from_history([restored]), {"platform-file-1"})
        restored_history = model_messages_to_history([restored])
        self.assertEqual(
            restored_history[0].parts[0].artifacts[0]["artifact_id"],
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
        store.write = AsyncMock(side_effect=OSError("platform down"))  # type: ignore[method-assign]
        result = await capability.after_tool_execute(
            _ctx(),
            call=ToolCallPart("query", {}, "call-1"),
            tool_def=ToolDefinition(name="query"),
            args={},
            result="A" * 30_000,
        )
        self.assertIsInstance(result, str)
        self.assertLessEqual(len(result), 8_200)

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
