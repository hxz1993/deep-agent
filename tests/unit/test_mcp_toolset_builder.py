from __future__ import annotations

import unittest

from fastmcp.exceptions import ToolError
from mcp import McpError
from mcp.types import ErrorData
from pydantic_ai import Agent, RunContext, ToolFailed
from pydantic_ai.exceptions import ModelRetry, RunCancelled
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    RetryPromptPart,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.history import model_messages_to_history
from deep_agent.capabilities.mcp.toolset_builder import (
    build_mcp_toolset,
    build_raw_url_mcp_toolset,
    resolve_mcp_init_timeout,
    resolve_mcp_max_retries,
)
from deep_agent.options import OPTION


class ResolveMcpInitTimeoutTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_timeout = OPTION.agent.mcp_init_timeout

    def tearDown(self) -> None:
        OPTION.agent.mcp_init_timeout = float(self._original_timeout)

    def test_uses_global_default(self) -> None:
        OPTION.agent.mcp_init_timeout = 42.0
        self.assertEqual(resolve_mcp_init_timeout({}), 42.0)

    def test_config_override_wins(self) -> None:
        OPTION.agent.mcp_init_timeout = 42.0
        self.assertEqual(resolve_mcp_init_timeout({"init_timeout": 15}), 15.0)


class ResolveMcpMaxRetriesTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_retries = OPTION.agent.request_retries

    def tearDown(self) -> None:
        OPTION.agent.request_retries = self._original_retries

    def test_uses_request_retries(self) -> None:
        OPTION.agent.request_retries = 3
        self.assertEqual(resolve_mcp_max_retries(), 3)


class BuildRawUrlMcpToolsetTests(unittest.TestCase):
    def setUp(self) -> None:
        self._original_timeout = OPTION.agent.mcp_init_timeout
        self._original_retries = OPTION.agent.request_retries

    def tearDown(self) -> None:
        OPTION.agent.mcp_init_timeout = float(self._original_timeout)
        OPTION.agent.request_retries = self._original_retries

    def test_passes_init_timeout_to_mcp_toolset(self) -> None:
        OPTION.agent.mcp_init_timeout = 25.0
        with unittest.mock.patch("deep_agent.capabilities.mcp.toolset_builder.MCPToolset") as mcp_toolset_cls:
            mcp_toolset_cls.return_value = unittest.mock.MagicMock()
            build_raw_url_mcp_toolset("deepcube", "http://127.0.0.1:19000/mcp")
            _, kwargs = mcp_toolset_cls.call_args
            self.assertEqual(kwargs["init_timeout"], 25.0)

    def test_passes_max_retries_to_mcp_toolset(self) -> None:
        OPTION.agent.request_retries = 4
        with unittest.mock.patch("deep_agent.capabilities.mcp.toolset_builder.MCPToolset") as mcp_toolset_cls:
            mcp_toolset_cls.return_value = unittest.mock.MagicMock()
            build_raw_url_mcp_toolset("deepcube", "http://127.0.0.1:19000/mcp")
            _, kwargs = mcp_toolset_cls.call_args
            self.assertEqual(kwargs["max_retries"], 4)

    def test_includes_server_instructions(self) -> None:
        with unittest.mock.patch("deep_agent.capabilities.mcp.toolset_builder.MCPToolset") as mcp_toolset_cls:
            mcp_toolset_cls.return_value = unittest.mock.MagicMock()
            build_raw_url_mcp_toolset("deepcube", "http://127.0.0.1:19000/mcp")
            _, kwargs = mcp_toolset_cls.call_args
            self.assertIs(kwargs["include_instructions"], True)


class BuildUrlMcpToolsetTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self._original_retries = OPTION.agent.request_retries
        OPTION.agent.request_retries = 2

    def tearDown(self) -> None:
        OPTION.agent.request_retries = self._original_retries

    def _inspection(self) -> dict:
        return {
            "status": "healthy",
            "tools": {
                "status": "supported",
                "count": 1,
                "snapshot_status": "ready",
                "snapshot_at": "2026-08-11T00:00:00Z",
                "snapshot_hash": "abc",
                "snapshot": {
                    "schema_version": "1.0",
                    "server_instructions": "Use the business catalog.",
                    "definitions": [
                        {
                            "name": "query",
                            "description": "Query records.",
                            "input_schema": {"type": "object"},
                            "output_schema": {"type": "object"},
                            "annotations": {"readOnlyHint": True},
                            "task_support": "optional",
                        }
                    ],
                },
            },
        }

    async def _call_pipeline_tool_with_error(self, error: Exception) -> tuple[Exception, int]:
        ctx = RunContext(
            deps=AgentDeps(session_id="session-a"),
            model=TestModel(),
            usage=RunUsage(),
            retry=0,
            max_retries=2,
        )
        inner = unittest.mock.MagicMock()
        inner.__aenter__ = unittest.mock.AsyncMock(return_value=inner)
        inner.__aexit__ = unittest.mock.AsyncMock(return_value=None)
        inner.call_tool = unittest.mock.AsyncMock(side_effect=error)
        with unittest.mock.patch(
            "deep_agent.capabilities.mcp.snapshot_toolset.MCPToolset",
            return_value=inner,
        ):
            toolset = build_mcp_toolset(
                "pipeline",
                "url",
                {"url": "http://127.0.0.1:8083/mcp"},
                inspection=self._inspection(),
            )
            run_toolset = await toolset.for_run(ctx)
            tools = await run_toolset.get_tools(ctx)
            try:
                await run_toolset.call_tool("pipeline_query", {}, ctx, tools["pipeline_query"])
            except Exception as raised:
                return raised, inner.call_tool.await_count
        self.fail("Expected the MCP tool call to fail")

    async def test_uses_snapshot_without_connecting(self) -> None:
        ctx = RunContext(
            deps=AgentDeps(session_id="session-a"),
            model=TestModel(),
            usage=RunUsage(),
        )
        with unittest.mock.patch(
            "deep_agent.capabilities.mcp.snapshot_toolset.MCPToolset"
        ) as mcp_toolset_cls:
            toolset = build_mcp_toolset(
                "biz",
                "url",
                {"url": "http://127.0.0.1:8083/mcp"},
                inspection=self._inspection(),
            )
            run_toolset = await toolset.for_run(ctx)
            async with run_toolset:
                tools = await run_toolset.get_tools(ctx)
                instructions = await run_toolset.get_instructions(ctx)

        mcp_toolset_cls.assert_not_called()
        self.assertEqual(list(tools), ["biz_query"])
        self.assertEqual(tools["biz_query"].tool_def.return_schema, {"type": "object"})
        self.assertEqual(instructions.content, "Use the business catalog.")

    async def test_connects_on_first_call_and_reuses_connection(self) -> None:
        ctx = RunContext(
            deps=AgentDeps(
                session_id="session-a",
                request_headers={"user": "4", "app": "app-a"},
            ),
            model=TestModel(),
            usage=RunUsage(),
        )
        inner = unittest.mock.MagicMock()
        inner.__aenter__ = unittest.mock.AsyncMock(return_value=inner)
        inner.__aexit__ = unittest.mock.AsyncMock(return_value=None)
        inner.call_tool = unittest.mock.AsyncMock(return_value={"ok": True})
        with unittest.mock.patch(
            "deep_agent.capabilities.mcp.snapshot_toolset.MCPToolset",
            return_value=inner,
        ) as mcp_toolset_cls:
            toolset = build_mcp_toolset(
                "biz",
                "url",
                {"url": "http://127.0.0.1:8083/mcp"},
                inspection=self._inspection(),
            )
            run_toolset = await toolset.for_run(ctx)
            async with run_toolset:
                tools = await run_toolset.get_tools(ctx)
                self.assertEqual(
                    await run_toolset.call_tool("biz_query", {}, ctx, tools["biz_query"]),
                    {"ok": True},
                )
                await run_toolset.call_tool("biz_query", {}, ctx, tools["biz_query"])

        mcp_toolset_cls.assert_called_once()
        _, kwargs = mcp_toolset_cls.call_args
        self.assertEqual(kwargs["headers"]["user"], "4")
        self.assertEqual(inner.__aenter__.await_count, 1)
        self.assertEqual(inner.call_tool.await_count, 2)

    async def test_connection_failure_becomes_stable_tool_failed(self) -> None:
        ctx = RunContext(
            deps=AgentDeps(session_id="session-a"),
            model=TestModel(),
            usage=RunUsage(),
        )
        inner = unittest.mock.MagicMock()
        inner.__aenter__ = unittest.mock.AsyncMock(side_effect=TimeoutError("secret"))
        inner.__aexit__ = unittest.mock.AsyncMock(return_value=None)
        with unittest.mock.patch(
            "deep_agent.capabilities.mcp.snapshot_toolset.MCPToolset",
            return_value=inner,
        ) as mcp_toolset_cls:
            toolset = build_mcp_toolset(
                "biz",
                "url",
                {"url": "http://127.0.0.1:8083/mcp"},
                inspection=self._inspection(),
            )
            run_toolset = await toolset.for_run(ctx)
            tools = await run_toolset.get_tools(ctx)
            with self.assertRaises(ToolFailed) as first:
                await run_toolset.call_tool("biz_query", {}, ctx, tools["biz_query"])
            with self.assertRaises(ToolFailed) as second:
                await run_toolset.call_tool("biz_query", {}, ctx, tools["biz_query"])

        self.assertNotIn("secret", str(first.exception))
        self.assertEqual(str(first.exception), str(second.exception))
        mcp_toolset_cls.assert_called_once()

    async def test_protocol_timeout_model_retry_becomes_tool_failed(self) -> None:
        ctx = RunContext(
            deps=AgentDeps(session_id="session-a"),
            model=TestModel(),
            usage=RunUsage(),
        )
        timeout = McpError(ErrorData(code=-32001, message="private timeout detail"))
        retry = ModelRetry("private timeout detail")
        retry.__cause__ = timeout
        inner = unittest.mock.MagicMock()
        inner.__aenter__ = unittest.mock.AsyncMock(return_value=inner)
        inner.__aexit__ = unittest.mock.AsyncMock(return_value=None)
        inner.call_tool = unittest.mock.AsyncMock(side_effect=retry)
        with unittest.mock.patch(
            "deep_agent.capabilities.mcp.snapshot_toolset.MCPToolset",
            return_value=inner,
        ):
            toolset = build_mcp_toolset(
                "biz",
                "url",
                {"url": "http://127.0.0.1:8083/mcp"},
                inspection=self._inspection(),
            )
            run_toolset = await toolset.for_run(ctx)
            tools = await run_toolset.get_tools(ctx)
            with self.assertRaises(ToolFailed) as raised:
                await run_toolset.call_tool("biz_query", {}, ctx, tools["biz_query"])

        self.assertNotIn("private timeout detail", str(raised.exception))
        self.assertIn("timed out or is temporarily unavailable", str(raised.exception))
        self.assertEqual(inner.call_tool.await_count, 1)

    async def test_canvas_deadline_model_retry_becomes_tool_failed(self) -> None:
        retry = ModelRetry("wait for canvas response: context deadline exceeded")
        raised, call_count = await self._call_pipeline_tool_with_error(retry)

        self.assertIsInstance(raised, ToolFailed)
        self.assertNotIn("context deadline exceeded", str(raised))
        self.assertIn("outcome may be unknown", str(raised))
        self.assertIn("Canvas snapshot", str(raised))
        self.assertEqual(call_count, 1)

    async def test_wrapped_canvas_deadline_model_retry_becomes_tool_failed(self) -> None:
        protocol_error = McpError(
            ErrorData(code=-32603, message="wait for canvas response: context deadline exceeded")
        )
        retry = ModelRetry("tool execution failed")
        retry.__cause__ = protocol_error
        raised, call_count = await self._call_pipeline_tool_with_error(retry)

        self.assertIsInstance(raised, ToolFailed)
        self.assertNotIn("context deadline exceeded", str(raised))
        self.assertIn("outcome may be unknown", str(raised))
        self.assertIn("Canvas snapshot", str(raised))
        self.assertEqual(call_count, 1)

    async def test_structured_canvas_timeout_requires_snapshot_before_retry(self) -> None:
        retry = ModelRetry(
            'CANVAS_ERROR {"code":"canvas_command_timeout",'
            '"outcome":"unknown","retryable":false,'
            '"next_action":{"tool":"pipeline_canvas_snapshot"}}'
        )
        raised, call_count = await self._call_pipeline_tool_with_error(retry)

        self.assertIsInstance(raised, ToolFailed)
        message = str(raised)
        self.assertNotIn("CANVAS_ERROR", message)
        self.assertIn("outcome may be unknown", message)
        self.assertIn("Canvas snapshot", message)
        self.assertEqual(call_count, 1)

    async def test_retryable_structured_canvas_error_remains_model_retry(self) -> None:
        retry = ModelRetry(
            'CANVAS_ERROR {"code":"canvas_validation_failed",'
            '"outcome":"rejected","retryable":true}'
        )
        raised, call_count = await self._call_pipeline_tool_with_error(retry)

        self.assertIsInstance(raised, ModelRetry)
        self.assertEqual(str(raised), str(retry))
        self.assertEqual(call_count, 1)

    async def test_business_model_retry_remains_retryable(self) -> None:
        ctx = RunContext(
            deps=AgentDeps(session_id="session-a"),
            model=TestModel(),
            usage=RunUsage(),
            retry=1,
            max_retries=2,
        )
        retry = ModelRetry("choose a unique workbook path")
        retry.__cause__ = ToolError("choose a unique workbook path")
        inner = unittest.mock.MagicMock()
        inner.__aenter__ = unittest.mock.AsyncMock(return_value=inner)
        inner.__aexit__ = unittest.mock.AsyncMock(return_value=None)
        inner.call_tool = unittest.mock.AsyncMock(side_effect=retry)
        with unittest.mock.patch(
            "deep_agent.capabilities.mcp.snapshot_toolset.MCPToolset",
            return_value=inner,
        ):
            toolset = build_mcp_toolset(
                "biz",
                "url",
                {"url": "http://127.0.0.1:8083/mcp"},
                inspection=self._inspection(),
            )
            run_toolset = await toolset.for_run(ctx)
            tools = await run_toolset.get_tools(ctx)
            with self.assertRaises(ModelRetry) as raised:
                await run_toolset.call_tool("biz_query", {}, ctx, tools["biz_query"])

        self.assertEqual(str(raised.exception), "choose a unique workbook path")
        self.assertEqual(inner.call_tool.await_count, 1)

    async def test_last_business_retry_is_persistable_failed_result_and_agent_finishes(self) -> None:
        retry = ModelRetry("startId 已存在，不应包含在新增节点列表中")
        inner = unittest.mock.MagicMock()
        inner.__aenter__ = unittest.mock.AsyncMock(return_value=inner)
        inner.__aexit__ = unittest.mock.AsyncMock(return_value=None)
        inner.call_tool = unittest.mock.AsyncMock(side_effect=retry)

        def model_function(messages, _info: AgentInfo) -> ModelResponse:
            failed = [
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart) and part.outcome == "failed"
            ]
            if failed:
                return ModelResponse(parts=[TextPart(content="本轮参数校验未通过，请发送新消息重试。")])
            retries = [
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, RetryPromptPart)
            ]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="pipeline_query",
                        args={"attempt": len(retries) + 1},
                        tool_call_id=f"canvas-validation-{len(retries) + 1}",
                    )
                ]
            )

        with unittest.mock.patch(
            "deep_agent.capabilities.mcp.snapshot_toolset.MCPToolset",
            return_value=inner,
        ):
            toolset = build_mcp_toolset(
                "pipeline",
                "url",
                {"url": "http://127.0.0.1:8083/mcp"},
                inspection=self._inspection(),
            )
            agent = Agent(
                FunctionModel(model_function),
                deps_type=AgentDeps,
                toolsets=[toolset],
            )
            result = await agent.run(
                "craft the canvas",
                deps=AgentDeps(session_id="canvas-validation-session"),
            )

        self.assertEqual(result.output, "本轮参数校验未通过，请发送新消息重试。")
        retries = [
            part
            for message in result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, RetryPromptPart)
        ]
        failed = [
            part
            for message in result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.outcome == "failed"
        ]
        self.assertEqual(len(retries), 2)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0].tool_call_id, "canvas-validation-3")
        self.assertIn("startId 已存在", str(failed[0].content))
        self.assertIn("本轮工具纠正次数已经耗尽", str(failed[0].content))
        self.assertEqual(inner.call_tool.await_count, 3)

        history = model_messages_to_history(result.all_messages())
        history_results = [
            part
            for message in history
            if message.role == "assistant"
            for part in message.parts
            if part.type == "tool_result"
        ]
        history_texts = [
            part.content
            for message in history
            if message.role == "assistant"
            for part in message.parts
            if part.type == "text"
        ]
        self.assertEqual(len(history_results), 1)
        self.assertEqual(history_results[0].tool_call_id, "canvas-validation-3")
        self.assertIn("startId 已存在", str(history_results[0].result))
        self.assertIn("本轮工具纠正次数已经耗尽", str(history_results[0].result))
        self.assertEqual(history_texts[-1], "本轮参数校验未通过，请发送新消息重试。")

    async def test_canvas_deadline_is_failed_result_and_agent_can_finish(self) -> None:
        retry = ModelRetry("wait for canvas response: context deadline exceeded")
        inner = unittest.mock.MagicMock()
        inner.__aenter__ = unittest.mock.AsyncMock(return_value=inner)
        inner.__aexit__ = unittest.mock.AsyncMock(return_value=None)
        inner.call_tool = unittest.mock.AsyncMock(side_effect=retry)

        def model_function(messages, _info: AgentInfo) -> ModelResponse:
            failed = [
                part
                for message in messages
                if isinstance(message, ModelRequest)
                for part in message.parts
                if isinstance(part, ToolReturnPart)
            ]
            if not failed:
                return ModelResponse(
                    parts=[
                        ToolCallPart(
                            tool_name="pipeline_query",
                            args={},
                            tool_call_id="canvas-timeout-call",
                        )
                    ]
                )
            self.assertEqual(failed[-1].outcome, "failed")
            return ModelResponse(parts=[TextPart(content="explained the temporary failure")])

        with unittest.mock.patch(
            "deep_agent.capabilities.mcp.snapshot_toolset.MCPToolset",
            return_value=inner,
        ):
            toolset = build_mcp_toolset(
                "pipeline",
                "url",
                {"url": "http://127.0.0.1:8083/mcp"},
                inspection=self._inspection(),
            )
            agent = Agent(
                FunctionModel(model_function),
                deps_type=AgentDeps,
                toolsets=[toolset],
            )
            result = await agent.run(
                "craft the canvas",
                deps=AgentDeps(session_id="canvas-timeout-session"),
            )

        self.assertEqual(result.output, "explained the temporary failure")
        returns = [
            part
            for message in result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        self.assertEqual(len(returns), 1)
        self.assertEqual(returns[0].tool_call_id, "canvas-timeout-call")
        self.assertEqual(returns[0].outcome, "failed")
        self.assertNotIn("context deadline exceeded", str(returns[0].content))
        self.assertEqual(inner.call_tool.await_count, 1)

    async def test_missing_snapshot_exposes_no_tools(self) -> None:
        ctx = RunContext(
            deps=AgentDeps(session_id="session-a"),
            model=TestModel(),
            usage=RunUsage(),
        )
        toolset = build_mcp_toolset(
            "biz",
            "url",
            {"url": "http://127.0.0.1:8083/mcp"},
        )
        self.assertEqual(await toolset.get_tools(ctx), {})

    async def test_run_cancelled_is_not_converted_to_tool_failed(self) -> None:
        ctx = RunContext(
            deps=AgentDeps(session_id="session-a"),
            model=TestModel(),
            usage=RunUsage(),
        )
        inner = unittest.mock.MagicMock()
        inner.__aenter__ = unittest.mock.AsyncMock(return_value=inner)
        inner.__aexit__ = unittest.mock.AsyncMock(return_value=None)
        inner.call_tool = unittest.mock.AsyncMock(side_effect=RunCancelled("stop"))
        with unittest.mock.patch(
            "deep_agent.capabilities.mcp.snapshot_toolset.MCPToolset",
            return_value=inner,
        ):
            toolset = build_mcp_toolset(
                "biz",
                "url",
                {"url": "http://127.0.0.1:8083/mcp"},
                inspection=self._inspection(),
            )
            run_toolset = await toolset.for_run(ctx)
            tools = await run_toolset.get_tools(ctx)
            with self.assertRaises(RunCancelled):
                await run_toolset.call_tool("biz_query", {}, ctx, tools["biz_query"])


if __name__ == "__main__":
    unittest.main()
