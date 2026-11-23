from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import PropertyMock, patch

import certifi
import httpx
from fastmcp.exceptions import ToolError
from mcp import McpError
from mcp.types import ErrorData
from pydantic_ai import Agent, RunContext, ToolFailed
from pydantic_ai.exceptions import ModelRetry, RunCancelled
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    ToolReturnPart,
)
from pydantic_ai.models.function import AgentInfo, FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage
from pydantic_ai.toolsets import FunctionToolset

from deep_agent.agent.history import is_tool_retry
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_names import READ_ONLY_TOOL
from deepfos_platform.conf.option import OptionNotSetError
from tests.helpers.conversation import project_model_messages
from deep_agent.capabilities.mcp.toolset_builder import (
    build_mcp_toolset,
    build_resource_client,
    build_raw_url_mcp_toolset,
    resolve_mcp_init_timeout,
    resolve_mcp_max_retries,
)
from deep_agent.options import OPTION
from deep_agent.capabilities.mcp.run_scoped import RunScopedMcpToolset
from deep_agent.capabilities.mcp.snapshot_toolset import _default_tls_context
from deep_agent.ptc import FilePayloadResolvingToolset, HybridCodeMode, build_code_mode_tool_package
from deep_agent.ptc.toolset import HybridCodeModeToolset


class McpTlsContextTests(unittest.TestCase):
    def test_default_bundle_reuses_context_until_replaced(self):
        with tempfile.TemporaryDirectory(prefix="mcp-tls-TMP-to-delete-") as tmp:
            original = certifi.where()
            ca = Path(tmp) / "ca.pem"
            shutil.copyfile(original, ca)
            with patch.dict(os.environ, {"SSL_CERT_FILE": "", "SSL_CERT_DIR": "", "SSLKEYLOGFILE": ""}):
                with patch("certifi.where", return_value=str(ca)), patch(
                    "deep_agent.capabilities.mcp.snapshot_toolset.fastmcp_version", "3.3.0",
                ):
                    first = _default_tls_context()
                    self.assertIsNotNone(first)
                    self.assertIs(first, _default_tls_context())
                    replacement = ca.with_name("new.pem")
                    shutil.copyfile(original, replacement)
                    replacement.replace(ca)
                    self.assertIsNot(first, _default_tls_context())
                    with patch.dict(os.environ, {"SSL_CERT_FILE": str(ca)}):
                        self.assertIsNone(_default_tls_context())


class McpInstructionIdentityTests(unittest.IsolatedAsyncioTestCase):
    async def test_builtin_server_instructions_reach_real_agent(self) -> None:
        inner = FunctionToolset(instructions="Use the builtin catalog.")

        class LeaseManager:
            @asynccontextmanager
            async def lease(self, *args, **kwargs):
                yield inner

        def model_function(messages, info):
            self.assertIn("Use the builtin catalog.", info.instructions)
            return ModelResponse(parts=[TextPart("ok")])

        toolset = RunScopedMcpToolset(
            server_name="builtin-service", config={}, lease_manager=LeaseManager(),
        ).prefixed("builtin-service")
        result = await Agent(FunctionModel(model_function), toolsets=[toolset]).run(
            "hello", deps=AgentDeps(session_id="instruction-test"),
        )
        self.assertEqual(result.output, "ok")

    def test_dynamic_client_ids_are_colon_free_and_distinct(self) -> None:
        identities = []
        for name in ("biz:name", "biz-name", "biz%3Aname"):
            clients = [
                build_mcp_toolset(name, "url", {"url": "https://example.invalid/mcp"}),
                build_resource_client(name, "url", {"url": "https://example.invalid/mcp"}),
                build_raw_url_mcp_toolset(name, "https://example.invalid/mcp"),
            ]
            for client in clients:
                self.assertNotIn(":", client.id)
                identities.append(client.id)
        self.assertEqual(len(identities), len(set(identities)))


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
    async def test_auth_failure_retains_status_and_logs_only_effective_context(self):
        from deep_agent.diagnostics.schema import safe_fields
        for mode, credential_header in (("inherit", "cookie"), ("inherit", "x-deepfos-key"), ("isolated", "x-deepfos-key")):
            with self.subTest(mode=mode, credential_header=credential_header):
                ctx = RunContext(deps=AgentDeps(session_id="channel-session", run_id="run-context",
                    request_headers={"app": "scope001", "space": "scope", "language": "zh-cn",
                                     credential_header: "credential-never-log"}), model=TestModel(), usage=RunUsage())
                inner = unittest.mock.MagicMock()
                response = httpx.Response(401, request=httpx.Request("POST", "https://example.invalid/mcp"))
                inner.__aenter__ = unittest.mock.AsyncMock(side_effect=httpx.HTTPStatusError(
                    "credential-never-log", request=response.request, response=response))
                inner.__aexit__ = unittest.mock.AsyncMock(return_value=None)
                with unittest.mock.patch("deep_agent.capabilities.mcp.snapshot_toolset.MCPToolset", return_value=inner), \
                     unittest.mock.patch("deep_agent.capabilities.mcp.snapshot_toolset.log_event") as log:
                    toolset = build_mcp_toolset("biz", "url", {
                        "url": "https://example.invalid/mcp", "request_context": {"mode": mode},
                    }, inspection=self._inspection())
                    run = await toolset.for_run(ctx)
                    tools = await run.get_tools(ctx)
                    for _ in range(2):
                        with self.assertRaises(ToolFailed) as failure:
                            await run.call_tool("mcp__biz__query", {}, ctx, tools["mcp__biz__query"])
                        self.assertIn("HTTP 401", str(failure.exception))
                        self.assertNotIn("temporarily unavailable", str(failure.exception))
                        self.assertNotIn("credential-never-log", str(failure.exception))
                log.assert_called_once()
                fields = log.call_args.kwargs
                self.assertNotIn("credential-never-log", str(fields))
                self.assertEqual(fields["auth_context"], credential_header if mode == "inherit" else "none")
                self.assertEqual(fields["app_id"], "scope001" if mode == "inherit" else None)
                self.assertEqual(safe_fields(fields)["auth_context"], fields["auth_context"])
                inner.__aenter__.assert_awaited_once()

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
                await run_toolset.call_tool("mcp__pipeline__query", {}, ctx, tools["mcp__pipeline__query"])
            except Exception as raised:
                return raised, inner.call_tool.await_count
        self.fail("Expected the MCP tool call to fail")

    async def test_only_trusted_platform_read_annotations_enable_buffering(self) -> None:
        ctx = RunContext(deps=AgentDeps(session_id="session-a"), model=TestModel(), usage=RunUsage())
        gateway = "https://platform.example/deepfos-server"
        platform_url = f"{gateway}/deepfos-mcp/mcp"
        scenarios = [
            (platform_url, True, None, True),
            (platform_url, False, None, False),
            (platform_url, True, "optional", False),
            ("https://third-party.example/mcp", True, None, False),
        ]
        platform = SimpleNamespace(server=SimpleNamespace(base=gateway))
        with patch("deep_agent.services.mcp_bound_app.PLATFORM_OPTION", platform):
            for url, read_only, task_support, expected in scenarios:
                with self.subTest(url=url, read_only=read_only, task_support=task_support):
                    inspection = self._inspection()
                    item = inspection["tools"]["snapshot"]["definitions"][0]
                    item["annotations"]["readOnlyHint"] = read_only
                    item["task_support"] = task_support
                    toolset = build_mcp_toolset("biz", "url", {"url": url}, inspection=inspection)
                    tools = await toolset.get_tools(ctx)
                    metadata = tools["mcp__biz__query"].tool_def.metadata
                    self.assertEqual(bool(metadata.get(READ_ONLY_TOOL)), expected)

    async def test_external_https_mcp_does_not_require_a_platform_gateway(self):
        inspection = self._inspection()
        inspection["tools"]["snapshot"]["definitions"][0]["annotations"]["readOnlyHint"] = True
        ctx = RunContext(deps=AgentDeps(session_id="session-a"), model=TestModel(), usage=RunUsage())
        with patch("deep_agent.services.mcp_bound_app.PLATFORM_OPTION") as platform:
            type(platform.server).base = PropertyMock(side_effect=OptionNotSetError("unconfigured"))
            toolset = build_mcp_toolset("external", "url", {"url": "https://external.example/mcp"},
                                       inspection=inspection)
            tools = await toolset.get_tools(ctx)
        self.assertNotIn(READ_ONLY_TOOL, tools["mcp__external__query"].tool_def.metadata)

    async def test_platform_cube_member_search_has_a_typed_ptc_catalog(self):
        ctx = RunContext(deps=AgentDeps(session_id="session-a"), model=TestModel(), usage=RunUsage())
        gateway = "https://platform.example/deepfos-server"
        platform = SimpleNamespace(server=SimpleNamespace(base=gateway))
        item_schema = {
            "type": "object",
            "properties": {"name": {"type": "string"}, "description": {"type": "string"}},
            "required": ["name"],
        }
        schema = {
            "type": "object",
            "properties": {
                "results": {
                    "type": "object",
                    "additionalProperties": {"type": ["null", "array"], "items": item_schema},
                },
                "query_statuses": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            "required": ["results", "query_statuses"],
        }
        inspection = self._inspection()
        item = inspection["tools"]["snapshot"]["definitions"][0]
        item.update(name="cube_search_members", output_schema=schema,
                    annotations={"readOnlyHint": True}, task_support=None)
        with patch("deep_agent.services.mcp_bound_app.PLATFORM_OPTION", platform):
            for url, trusted in (
                (f"{gateway}/deepfos-mcp/mcp", True),
                ("https://third-party.example/mcp", False),
            ):
                with self.subTest(url=url):
                    native = build_mcp_toolset("biz", "url", {"url": url}, inspection=inspection)
                    wrapped = FilePayloadResolvingToolset(native, server_names=("biz",))
                    tools = await wrapped.get_tools(ctx)
                    definition = tools["mcp__biz__cube_search_members"].tool_def
                    hybrid = HybridCodeModeToolset(wrapped=wrapped)
                    callable_defs, _ = hybrid._partition_callable_tools(tools)
                    stubs = hybrid._build_type_check_stubs(callable_defs)
                    self.assertEqual(definition.return_schema, schema)
                    self.assertEqual(
                        definition.return_schema["properties"]["results"]["additionalProperties"]["type"],
                        ["null", "array"],
                    )
                    descriptions = []

                    def respond(_messages, info):
                        descriptions.extend(tool.description or "" for tool in info.function_tools
                                            if tool.name == "run_code")
                        return ModelResponse(parts=[TextPart("done")])

                    agent = Agent(
                        FunctionModel(respond), deps_type=AgentDeps,
                        toolsets=[build_code_mode_tool_package().toolset, wrapped],
                        capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True)],
                    )
                    await agent.run("Inspect the PTC catalog.", deps=AgentDeps(session_id="catalog"))
                    self.assertEqual(len(descriptions), 1)
                    if trusted:
                        for rendered in (stubs, descriptions[0]):
                            self.assertRegex(rendered, r"results: dict\[str, list\[\w+\] \| None\]")
                            self.assertIn("description: NotRequired[str]", rendered)
                            self.assertIn("query_statuses: dict[str, str]", rendered)
                    else:
                        self.assertIsNone(callable_defs["biz_cube_search_members"].return_schema)
                        self.assertNotIn("Member-search return types:", descriptions[0])

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
        self.assertEqual(list(tools), ["mcp__biz__query"])
        self.assertEqual(tools["mcp__biz__query"].tool_def.return_schema, {"type": "object"})
        self.assertIs(tools["mcp__biz__query"].tool_def.strict, False)
        self.assertEqual([part.content for part in instructions], ["Use the business catalog."])

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
                    await run_toolset.call_tool("mcp__biz__query", {}, ctx, tools["mcp__biz__query"]),
                    {"ok": True},
                )
                await run_toolset.call_tool("mcp__biz__query", {}, ctx, tools["mcp__biz__query"])

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
                await run_toolset.call_tool("mcp__biz__query", {}, ctx, tools["mcp__biz__query"])
            with self.assertRaises(ToolFailed) as second:
                await run_toolset.call_tool("mcp__biz__query", {}, ctx, tools["mcp__biz__query"])

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
                await run_toolset.call_tool("mcp__biz__query", {}, ctx, tools["mcp__biz__query"])

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
                await run_toolset.call_tool("mcp__biz__query", {}, ctx, tools["mcp__biz__query"])

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
                if is_tool_retry(part)
            ]
            return ModelResponse(
                parts=[
                    ToolCallPart(
                        tool_name="mcp__pipeline__query",
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
            if is_tool_retry(part)
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

        conversation = project_model_messages(result.all_messages())
        history_results = [
            item
            for item in conversation
            if item.kind == "tool" and item.status != "removed"
        ]
        history_texts = [item.text for item in conversation if item.kind == "assistant_message" and item.text]
        self.assertEqual(len(history_results), 1)
        self.assertEqual(history_results[0].call_id, "canvas-validation-3")
        self.assertIn("startId 已存在", str(history_results[0].output))
        self.assertIn("本轮工具纠正次数已经耗尽", str(history_results[0].output))
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
                            tool_name="mcp__pipeline__query",
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
                await run_toolset.call_tool("mcp__biz__query", {}, ctx, tools["mcp__biz__query"])


if __name__ == "__main__":
    unittest.main()
