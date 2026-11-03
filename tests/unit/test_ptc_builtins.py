from __future__ import annotations

import unittest
from unittest.mock import patch

from pydantic_ai import FunctionToolset, RunContext, Tool
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.factory import _toolsets_for_spec, build_agent
from deep_agent.agent.spec import AgentSpec
from deep_agent.capabilities import build_registry
from deep_agent.capabilities.registry import ToolPackage
from deep_agent.files.export_toolset import build_file_export_toolset
from deep_agent.ptc.builtin import BuiltinPtcToolset
from deep_agent.ptc.toolset import HybridCodeModeToolset


_ALLOWED = {
    "execute_python", "web_search", "web_fetch", "execute_skill_script",
    "export_file_to_download_center",
}


def _context(*, user="fixture-user") -> RunContext[AgentDeps]:
    return RunContext(
        deps=AgentDeps(session_id="builtin-ptc", user=user),
        model=TestModel(), usage=RunUsage(),
    )


def _named_tools(*names, calls=None, prepare=None):
    def make(name):
        async def invoke(value: int = 1) -> dict[str, int]:
            if calls is not None:
                calls.append((name, value))
            return {"value": value}

        return Tool(invoke, name=name, prepare=prepare, metadata={"source": "kept"})

    return FunctionToolset([make(name) for name in names], include_return_schema=True)


def _assembly(*, ptc=True, calls=None):
    registry = build_registry()
    registry.register(ToolPackage(
        code="builtin:sandbox", category="builtin",
        toolset=_named_tools("execute_python", calls=calls),
    ))
    spec = AgentSpec(
        name="fixture", model="test", instructions="Use tools when requested.",
        capabilities=["builtin:general", "builtin:sandbox", "builtin:web", "builtin:document"]
        + (["builtin:ptc"] if ptc else []),
        skills=["fixture"],
    )
    kwargs = {
        "web_toolset": _named_tools("web_search", "web_fetch", calls=calls),
        "image_view_toolset": _named_tools("view_image", calls=calls),
        "file_toolset": _named_tools("read_file", calls=calls),
        "document_toolset": _named_tools("parse_document", calls=calls),
        "skill_script_toolset": _named_tools("execute_skill_script", calls=calls),
        "file_export_toolset": _named_tools("export_file_to_download_center", calls=calls),
    }
    return registry, spec, kwargs


class BuiltinPtcRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_only_allowed_tools_are_hybrid_and_file_loader_stays_code_only(self):
        registry, spec, kwargs = _assembly()
        tools = {}
        for toolset in _toolsets_for_spec(registry, spec, **kwargs):
            tools.update(await toolset.get_tools(_context()))
        for name in _ALLOWED:
            with self.subTest(name=name):
                metadata = tools[name].tool_def.metadata
                self.assertEqual(metadata["deepagent_code_mode"], "hybrid")
                self.assertIs(metadata["deepagent_ptc_builtin"], True)
                self.assertEqual(metadata["source"], "kept")
                self.assertIsNotNone(tools[name].tool_def.return_schema)
        for name in ("get_current_time", "view_image", "read_file", "parse_document"):
            self.assertNotIn("deepagent_ptc_builtin", tools[name].tool_def.metadata or {})
            self.assertNotIn("deepagent_code_mode", tools[name].tool_def.metadata or {})
        self.assertEqual(tools["load_file_data"].tool_def.metadata["deepagent_code_mode"], "code_only")

    async def test_native_return_schema_is_preserved_and_only_ptc_copy_is_generic(self):
        source = _named_tools("execute_python")
        ctx = _context()
        source_def = (await source.get_tools(ctx))["execute_python"].tool_def
        self.assertEqual(source_def.return_schema["type"], "object")
        self.assertEqual(source_def.return_schema["additionalProperties"], {"type": "integer"})

        wrapped = BuiltinPtcToolset(source, names=frozenset({"execute_python"}))
        tools = await wrapped.get_tools(ctx)
        native_def = tools["execute_python"].tool_def
        self.assertEqual(native_def.return_schema, source_def.return_schema)
        self.assertEqual(native_def.parameters_json_schema, source_def.parameters_json_schema)

        callable_defs, _aliases = HybridCodeModeToolset(wrapped=wrapped)._partition_callable_tools(tools)
        self.assertIsNone(callable_defs["execute_python"].return_schema)
        self.assertEqual(native_def.return_schema, source_def.return_schema)
        self.assertEqual((await source.get_tools(ctx))["execute_python"].tool_def.return_schema,
                         source_def.return_schema)

    async def test_disabling_ptc_leaves_original_toolsets_and_schemas_unchanged(self):
        registry, spec, kwargs = _assembly(ptc=False)
        assembled = _toolsets_for_spec(registry, spec, **kwargs)
        self.assertIn(registry.get("builtin:sandbox").toolset, assembled)
        for source in kwargs.values():
            self.assertIn(source, assembled)
        for toolset in assembled:
            for name, tool in (await toolset.get_tools(_context())).items():
                self.assertNotIn("deepagent_ptc_builtin", tool.tool_def.metadata or {})
                if name in _ALLOWED:
                    self.assertIsNotNone(tool.tool_def.return_schema)

    async def test_same_names_from_other_sources_do_not_gain_ptc_permission(self):
        registry = build_registry()
        impostor = _named_tools("execute_python", "web_search", "execute_skill_script")
        spec = AgentSpec(
            name="fixture", model="test", instructions="", capabilities=["builtin:ptc", "ui:fixture"],
        )
        for source in (
            {"file_toolset": impostor}, {"mcp_resource_toolset": impostor},
            {"history_toolset": impostor}, {"channel_toolset": impostor},
            {"skill_resource_toolset": impostor},
            {"dynamic_ui_packages": [ToolPackage(code="ui:fixture", category="ui", toolset=impostor)]},
        ):
            with self.subTest(source=next(iter(source))):
                assembled = _toolsets_for_spec(registry, spec, **source)
                for toolset in assembled:
                    for name, tool in (await toolset.get_tools(_context())).items():
                        if name != "load_file_data":
                            self.assertNotIn("deepagent_ptc_builtin", tool.tool_def.metadata or {})
                            self.assertNotIn("deepagent_code_mode", tool.tool_def.metadata or {})

    async def test_prepare_filter_and_original_input_validator_remain_in_effect(self):
        async def prepare(ctx, tool_def):
            return tool_def if ctx.deps.user == "fixture-user" else None

        registry = build_registry()
        original = _named_tools("web_search", prepare=prepare)
        spec = AgentSpec(name="fixture", model="test", instructions="", capabilities=["builtin:ptc", "builtin:web"])
        wrapped = _toolsets_for_spec(registry, spec, web_toolset=original)[-1]
        self.assertEqual(await wrapped.get_tools(_context(user="other-user")), {})
        source_tool = (await original.get_tools(_context()))["web_search"]
        tool = (await wrapped.get_tools(_context()))["web_search"]
        self.assertIs(tool.args_validator, source_tool.args_validator)
        self.assertEqual(tool.tool_def.parameters_json_schema, source_tool.tool_def.parameters_json_schema)
        with self.assertRaises(ValueError):
            tool.args_validator.validate_python({"value": "invalid-int"})

    async def test_unselected_sandbox_and_missing_runtime_tools_are_not_added(self):
        registry, spec, _kwargs = _assembly()
        spec.capabilities.remove("builtin:sandbox")
        tools = {}
        for toolset in _toolsets_for_spec(registry, spec):
            tools.update(await toolset.get_tools(_context()))
        self.assertTrue(_ALLOWED.isdisjoint(tools))

    async def test_real_download_export_context_filter_is_preserved(self):
        registry = build_registry()
        spec = AgentSpec(name="fixture", model="test", instructions="", capabilities=["builtin:ptc"])
        wrapped = _toolsets_for_spec(
            registry, spec, file_export_toolset=build_file_export_toolset(),
        )[-1]
        self.assertEqual(await wrapped.get_tools(_context()), {})
        authorized = _context()
        authorized.deps.request_headers.update({"app": "fixture001", "space": "fixture"})
        tools = await wrapped.get_tools(authorized)
        self.assertEqual(set(tools), {"export_file_to_download_center"})
        self.assertIs(tools["export_file_to_download_center"].tool_def.metadata["deepagent_ptc_builtin"], True)

    async def test_native_and_real_monty_calls_share_original_dispatch(self):
        calls = []
        registry, spec, kwargs = _assembly(calls=calls)
        step = 0

        def respond(messages, info):
            nonlocal step
            step += 1
            native_names = {tool.name for tool in info.function_tools}
            self.assertTrue(_ALLOWED <= native_names)
            self.assertNotIn("load_file_data", native_names)
            if step == 1:
                return ModelResponse(parts=[ToolCallPart("execute_python", {"value": 7}, "native")])
            if step == 2:
                return ModelResponse(parts=[ToolCallPart("run_code", {"code": (
                    "a = await execute_python(value=2)\n"
                    "b = await web_search(value=3)\n"
                    "c = await web_fetch(value=4)\n"
                    "d = await execute_skill_script(value=5)\n"
                    "e = await export_file_to_download_center(value=6)\n"
                    "a['value'] + b['value'] + c['value'] + d['value'] + e['value']"
                )}, "ptc")])
            ptc_return = next(part for message in messages for part in message.parts
                              if isinstance(part, ToolReturnPart) and part.tool_call_id == "ptc")
            self.assertEqual(ptc_return.outcome, "success")
            self.assertIn("20", str(ptc_return.content))
            return ModelResponse(parts=[TextPart("done")])

        with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(respond)):
            agent = build_agent(spec, registry, **kwargs)
        result = await agent.run("exercise both routes", deps=AgentDeps(session_id="builtin-ptc", user="fixture-user"))
        self.assertEqual(result.output, "done")
        self.assertEqual(calls, [
            ("execute_python", 7), ("execute_python", 2), ("web_search", 3),
            ("web_fetch", 4), ("execute_skill_script", 5), ("export_file_to_download_center", 6),
        ])


if __name__ == "__main__":
    unittest.main()
