from __future__ import annotations

import unittest
from datetime import UTC, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfoNotFoundError

from pydantic_ai import ModelRetry, RunContext
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.history import is_tool_retry
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.factory import _toolsets_for_spec, build_agent
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.environment import build_environment_toolset
from deep_agent.capabilities.codes import canonical_tool_package_codes


class RuntimeEnvironmentTests(unittest.IsolatedAsyncioTestCase):
    def context(self):
        return RunContext(deps=AgentDeps(user="fixture", session_id="runtime-time"), model=TestModel(), usage=RunUsage())

    async def test_default_and_explicit_timezones_keep_iso_string_and_dst(self):
        tools = await build_environment_toolset().get_tools(self.context())
        tool = tools["get_current_time"]
        schema = tool.tool_def.parameters_json_schema
        self.assertNotIn("timezone", schema.get("required", []))
        self.assertEqual(schema["properties"]["timezone"]["default"], "UTC")
        class FrozenDatetime(datetime):
            instant = datetime(2026, 1, 5, 12, 0, tzinfo=UTC)
            @classmethod
            def now(cls, tz=None):
                return cls.instant.astimezone(tz)
        with patch("deep_agent.capabilities.environment.datetime", FrozenDatetime):
            self.assertEqual(await tool.call_func({}, self.context()), "2026-01-05T12:00:00+00:00")
            self.assertEqual(await tool.call_func({"timezone": "Asia/Shanghai"}, self.context()), "2026-01-05T20:00:00+08:00")
            self.assertEqual(await tool.call_func({"timezone": "America/New_York"}, self.context()), "2026-01-05T07:00:00-05:00")
            FrozenDatetime.instant = datetime(2026, 7, 5, 12, 0, tzinfo=UTC)
            self.assertEqual(await tool.call_func({"timezone": "America/New_York"}, self.context()), "2026-07-05T08:00:00-04:00")
            self.assertEqual(await tool.call_func({}, self.context()), "2026-07-05T12:00:00+00:00")

    async def test_invalid_or_unavailable_zone_is_not_silently_replaced(self):
        tool = (await build_environment_toolset().get_tools(self.context()))["get_current_time"]
        for zone in ("", " ", "Not/A_Zone", "../UTC", "/etc/localtime"):
            with self.subTest(zone=zone), self.assertRaises(ModelRetry):
                await tool.call_func({"timezone": zone}, self.context())
        with patch("deep_agent.capabilities.environment.ZoneInfo", side_effect=ZoneInfoNotFoundError("missing database")):
            with self.assertRaises(ModelRetry):
                await tool.call_func({"timezone": "Asia/Shanghai"}, self.context())
            self.assertEqual(datetime.fromisoformat(await tool.call_func({}, self.context())).utcoffset().total_seconds(), 0)

    async def test_time_is_always_available_once_but_not_a_ptc_function(self):
        for caps in ([], ["builtin:general"], ["builtin:general", "builtin:ptc"], ["builtin:ptc"]):
            tools = []
            for toolset in _toolsets_for_spec(build_registry(), AgentSpec(name="fixture", model="test", instructions="", capabilities=caps)):
                tools.extend((await toolset.get_tools(self.context())).values())
            times = [tool for tool in tools if tool.tool_def.name == "get_current_time"]
            self.assertEqual(len(times), 1)
            self.assertNotIn("deepagent_ptc_builtin", times[0].tool_def.metadata or {})
        self.assertEqual(canonical_tool_package_codes(["builtin:general", "builtin:web", "builtin:code-mode", "builtin:ptc", "mcp:fixture"]), ["builtin:web", "builtin:ptc", "mcp:fixture"])
        self.assertNotIn("builtin:general", build_registry().codes())

    async def test_sdk_executes_default_time_tool_with_no_selected_capabilities(self):
        observed = []
        def model(messages, info):
            observed.append(info.function_tools)
            results = [part for message in messages for part in message.parts if isinstance(part, ToolReturnPart) and part.tool_name == "get_current_time"]
            if not results:
                return ModelResponse(parts=[ToolCallPart("get_current_time", {"timezone": "Asia/Shanghai"})])
            return ModelResponse(parts=[TextPart(str(results[-1].content))])
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(model)):
            agent = build_agent(AgentSpec(name="fixture", model="test", instructions="", capabilities=[]), build_registry())
            result = await agent.run("获取北京时间", deps=self.context().deps)
        self.assertEqual(datetime.fromisoformat(result.output).utcoffset().total_seconds(), 8 * 3600)
        for tools in observed:
            self.assertEqual([tool.name for tool in tools].count("get_current_time"), 1)

    async def test_sdk_can_correct_invalid_timezone_and_continue_same_run(self):
        calls = []
        def model(messages, info):
            del info
            parts = [part for message in messages for part in message.parts]
            if any(isinstance(part, ToolReturnPart) and part.tool_name == "get_current_time"
                   and part.outcome == "success" for part in parts):
                return ModelResponse(parts=[TextPart("完成")])
            corrected = any(is_tool_retry(part) for part in parts)
            zone = "UTC" if corrected else "Not/A_Zone"
            calls.append(zone)
            return ModelResponse(parts=[ToolCallPart("get_current_time", {"timezone": zone})])
        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(model)):
            agent = build_agent(AgentSpec(name="fixture", model="test", instructions=""), build_registry())
            result = await agent.run("查询当前时间", deps=self.context().deps)
        self.assertEqual(calls, ["Not/A_Zone", "UTC"])
        self.assertEqual(result.output, "完成")
