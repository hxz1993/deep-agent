from __future__ import annotations

import unittest

from fastmcp.exceptions import ToolError
from mcp import McpError
from mcp.types import ErrorData
from pydantic_ai import Agent, RunContext, ToolFailed
from pydantic_ai.exceptions import ModelRetry, RunCancelled
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
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

    async def test_business_model_retry_remains_retryable(self) -> None:
        ctx = RunContext(
            deps=AgentDeps(session_id="session-a"),
            model=TestModel(),
            usage=RunUsage(),
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

    async def test_protocol_timeout_is_failed_result_and_agent_can_finish(self) -> None:
        timeout = McpError(ErrorData(code=-32001, message="private timeout detail"))
        retry = ModelRetry("private timeout detail")
        retry.__cause__ = timeout
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
                            tool_name="biz_query",
                            args={},
                            tool_call_id="timeout-call",
                        )
                    ]
                )
            self.assertEqual(failed[-1].outcome, "failed")
            return ModelResponse(parts=[TextPart(content="used a safe fallback")])

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
            agent = Agent(
                FunctionModel(model_function),
                deps_type=AgentDeps,
                toolsets=[toolset],
            )
            result = await agent.run(
                "query the service",
                deps=AgentDeps(session_id="timeout-session"),
            )

        self.assertEqual(result.output, "used a safe fallback")
        returns = [
            part
            for message in result.all_messages()
            if isinstance(message, ModelRequest)
            for part in message.parts
            if isinstance(part, ToolReturnPart)
        ]
        self.assertEqual(len(returns), 1)
        self.assertEqual(returns[0].tool_call_id, "timeout-call")
        self.assertEqual(returns[0].outcome, "failed")
        self.assertNotIn("private timeout detail", str(returns[0].content))
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
