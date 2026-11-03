from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx2
from pydantic_ai import Agent, RunContext
from pydantic_ai.exceptions import UnexpectedModelBehavior
from pydantic_ai.messages import ModelRequest, SystemPromptPart, UserPromptPart, VideoUrl
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.output import OutputObjectDefinition
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.documents.settings import DocumentParsingRuntimeConfig
from deep_agent.documents.toolset import build_document_toolset
from deep_agent.files.image_view.toolset import build_view_image_toolset
from deep_agent.files.toolset import build_file_toolset
from deep_agent.models.provider_registry import ProviderRuntimeConfig, resolve_provider_model
from deep_agent.models.tool_schema_refs import INLINE_TOOL_SCHEMA_REFS_TAG, inline_tool_schema_refs
from deep_agent.skills.runtime import ResolvedSkill


REF = "#/$defs/Item"
SCHEMA = {
    "type": "object",
    "properties": {"item": {"$ref": REF}},
    "required": ["item"],
    "additionalProperties": False,
    "$defs": {
        "Item": {"type": "object", "properties": {"value": {"type": "integer"}},
                 "required": ["value"], "additionalProperties": False},
    },
}


def model_for(*, enabled=False, video=False, merge=False, provider_type="openai_compatible"):
    return resolve_provider_model(
        model_name="qwen3.8-flash" if provider_type == "alibaba" else "custom-model",
        provider=ProviderRuntimeConfig(
            id="probe", name="probe", provider_type=provider_type,
            use_default_base_url=provider_type != "openai_compatible",
            base_url="https://schema-probe.invalid/v1", api_key="placeholder",
        ),
        input_modalities=["text", "video"] if video else ["text"],
        tags=([INLINE_TOOL_SCHEMA_REFS_TAG] if enabled else []) + (["single_system_message"] if merge else []),
    )


def completion(message, *, streaming, finish="stop"):
    common = {"id": "probe", "model": "custom-model", "created": 1}
    if not streaming:
        return httpx2.Response(200, json={**common, "object": "chat.completion",
            "choices": [{"index": 0, "message": message, "finish_reason": finish}]})
    chunks = [
        {**common, "object": "chat.completion.chunk",
         "choices": [{"index": 0, "delta": message, "finish_reason": None}]},
        {**common, "object": "chat.completion.chunk",
         "choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
    ]
    return httpx2.Response(200, headers={"content-type": "text/event-stream"},
                          content="".join(f"data: {json.dumps(chunk)}\n\n" for chunk in chunks) + "data: [DONE]\n\n")


def mock_provider(client):
    provider = OpenAIProvider(base_url="https://schema-probe.invalid/v1", api_key="test", http_client=client)
    return patch("deep_agent.models.provider_registry._openai_style_provider", return_value=provider)


class InlineToolSchemaTests(unittest.TestCase):
    def test_constraints_and_data_annotations_survive_without_mutation(self):
        schema = copy.deepcopy(SCHEMA)
        schema["properties"]["item"].update(description="site description", default={"$ref": "literal"})
        schema["properties"]["escaped"] = {"$ref": "#/$defs/x~1y~0"}
        schema["$defs"]["x/y~"] = {"type": "integer", "minimum": 0}
        before = copy.deepcopy(schema)
        actual = inline_tool_schema_refs(schema)
        self.assertNotIn("$defs", actual)
        self.assertEqual(actual["properties"]["item"], {
            **schema["$defs"]["Item"], "description": "site description", "default": {"$ref": "literal"},
        })
        self.assertEqual(actual["properties"]["escaped"], {"type": "integer", "minimum": 0})
        actual["properties"]["item"]["required"].append("new")
        self.assertEqual(schema, before)

    def test_falls_back_atomically_for_unsupported_or_over_budget_schemas(self):
        for bad in (
            {"$ref": "https://schemas.invalid/item"}, {"$ref": "#/$defs/missing"},
            {"$ref": REF, "minProperties": 2}, {"$ref": REF, "$id": "child"},
            {"$dynamicRef": "#item"}, {"customSchema": {"$ref": REF}},
            {"description": "界" * 25_000},
            {"enum": list(range(2048))},
        ):
            with self.subTest(keys=list(bad)):
                schema = copy.deepcopy(SCHEMA)
                schema["properties"]["second"] = bad
                self.assertIs(inline_tool_schema_refs(schema), schema)
        schema = copy.deepcopy(SCHEMA)
        schema["$defs"]["Item"]["properties"]["child"] = {"$ref": REF}
        self.assertIs(inline_tool_schema_refs(schema), schema)
        schema = copy.deepcopy(SCHEMA)
        for _ in range(8):
            schema["$defs"]["Item"] = {"type": "array", "items": schema["$defs"]["Item"]}
        for _ in range(8):
            schema["properties"]["item"] = {"type": "array", "items": schema["properties"]["item"]}
        self.assertIs(inline_tool_schema_refs(schema), schema)

    def test_other_providers_keep_existing_model_and_profile(self):
        for provider in ("openai", "alibaba", "anthropic"):
            with self.subTest(provider=provider):
                off = model_for(provider_type=provider)
                on = model_for(provider_type=provider, enabled=True)
                self.assertIs(type(off), type(on))
                self.assertEqual(off.profile, on.profile)

    def test_long_acyclic_reference_chain_falls_back_before_python_stack_exhaustion(self):
        chain_length = 1050
        definitions = {f"ref{i}": {"$ref": f"#/$defs/ref{i + 1}"} for i in range(chain_length)}
        definitions[f"ref{chain_length}"] = {"type": "object"}
        schema = {"$ref": "#/$defs/ref0", "$defs": definitions}
        self.assertIs(inline_tool_schema_refs(schema), schema)

    def test_malformed_uri_fragment_does_not_resolve_to_replacement_character(self):
        schema = {"$ref": "#/$defs/%FF", "$defs": {"�": {"type": "object"}}}
        self.assertIs(inline_tool_schema_refs(schema), schema)


class ToolSchemaWireTests(unittest.IsolatedAsyncioTestCase):
    async def capture(self, *, enabled, streaming, params, video=False, merge=False):
        requests = []

        def respond(request):
            requests.append(json.loads(request.content))
            return completion({"role": "assistant", "content": "ok"}, streaming=streaming)

        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            with mock_provider(client):
                model = model_for(enabled=enabled, video=video, merge=merge)
            prompt = [VideoUrl("https://media.invalid/sample.mp4"), "Inspect"] if video else "Inspect"
            messages = [ModelRequest(parts=[SystemPromptPart("A"), SystemPromptPart("B"), UserPromptPart(prompt)])]
            if streaming:
                async with model.request_stream(messages, None, params) as stream:
                    async for _ in stream:
                        pass
            else:
                await model.request(messages, None, params)
        self.assertEqual(len(requests), 1)
        return requests[0]

    async def test_sdk_wire_changes_only_regular_tool_parameters_in_stream_and_sync(self):
        ctx = RunContext(deps=AgentDeps(session_id="probe"), model=model_for(), usage=RunUsage())
        definitions = []
        read_tool = build_file_toolset().wrapped.tools["read_file"]
        definitions.append(read_tool.tool_def)
        for toolset in (
            build_document_toolset(DocumentParsingRuntimeConfig("unconfigured")),
            build_view_image_toolset({}, web_authorized=True, python_available=False),
        ):
            definitions.extend(tool.tool_def for tool in (await toolset.get_tools(ctx)).values())
        self.assertEqual([tool.name for tool in definitions], ["read_file", "parse_document", "view_image"])
        definitions.extend([
            ToolDefinition(name="mcp_fixture", parameters_json_schema=SCHEMA, strict=True),
            ToolDefinition(name="simple", parameters_json_schema={"type": "object", "properties": {"n": {"type": "integer"}}}),
        ])
        output = ToolDefinition(name="submit_result", parameters_json_schema=SCHEMA, kind="output")
        params = ModelRequestParameters(function_tools=definitions, output_tools=[output], output_mode="tool")
        original = copy.deepcopy(params)
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                off = await self.capture(enabled=False, streaming=streaming, params=params)
                on = await self.capture(enabled=True, streaming=streaming, params=params)
                tools_off = {t["function"]["name"]: t["function"] for t in off["tools"]}
                tools_on = {t["function"]["name"]: t["function"] for t in on["tools"]}
                for name in ("read_file", "parse_document", "view_image", "mcp_fixture"):
                    before, after = tools_off[name], tools_on[name]
                    self.assertIn("$defs", before["parameters"])
                    self.assertNotIn("$defs", after["parameters"])
                    self.assertNotIn('"$ref"', json.dumps(after["parameters"]))
                    self.assertEqual(before.get("strict"), after.get("strict"))
                    after["parameters"] = before["parameters"]
                self.assertEqual(off, on)
        self.assertEqual(params, original)

    async def test_native_output_video_and_system_message_switch_are_independent(self):
        params = ModelRequestParameters(
            function_tools=[ToolDefinition(name="probe", parameters_json_schema=SCHEMA)],
            output_object=OutputObjectDefinition(json_schema=SCHEMA),
            output_mode="native",
        )
        off = await self.capture(enabled=False, streaming=False, params=params, video=True)
        on = await self.capture(enabled=True, streaming=False, params=params, video=True, merge=True)
        self.assertEqual(off["response_format"], on["response_format"])
        self.assertEqual(off["messages"][-1], on["messages"][-1])
        self.assertEqual(on["messages"][-1]["content"][0]["type"], "video_url")
        self.assertEqual([m["role"] for m in on["messages"]], ["system", "user"])
        self.assertNotIn("$defs", on["tools"][0]["function"]["parameters"])
        back = await self.capture(enabled=False, streaming=False, params=params, video=True, merge=True)
        self.assertEqual(back["tools"], off["tools"])
        self.assertEqual(back["messages"], on["messages"])

    async def test_gateway_shape_reproduces_validation_failure_then_real_read_file_succeeds(self):
        content = b"# Local fixture\nVerified read.\n"
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            resource_path = "references/check.md"
            (root / resource_path).parent.mkdir()
            (root / resource_path).write_bytes(content)
            skill = ResolvedSkill(code="fixture", source="upload", package_root=root, library_root=root,
                resource_index=[{"path": resource_path, "category": "reference", "mime": "text/markdown",
                                 "readable": True, "size": len(content),
                                 "content_hash": hashlib.sha256(content).hexdigest()}])
            resource = {"type": "skill", "skill_code": "fixture", "path": resource_path}
            for streaming in (False, True):
                for enabled in (False, True):
                    with self.subTest(streaming=streaming, enabled=enabled):
                        requests = []

                        def respond(request):
                            body = json.loads(request.content)
                            requests.append(body)
                            if len(requests) == 2:
                                return completion({"role": "assistant", "content": "Done."}, streaming=streaming)
                            schema = body["tools"][0]["function"]["parameters"]
                            branches = schema["properties"]["resource"]["oneOf"]
                            supplied = resource if all(b.get("type") == "object" for b in branches) else json.dumps(resource)
                            call = {"id": "call-read", "type": "function",
                                    "function": {"name": "read_file", "arguments": json.dumps({"resource": supplied})}}
                            if streaming:
                                call["index"] = 0
                            return completion({"role": "assistant", "tool_calls": [call]},
                                              streaming=streaming, finish="tool_calls")

                        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
                            with mock_provider(client):
                                model = model_for(enabled=enabled)
                            agent = Agent(model, toolsets=[build_file_toolset({"fixture": skill})], retries=0)
                            deps = AgentDeps(session_id="wire-read")

                            async def run():
                                if streaming:
                                    async with agent.run_stream("Read the fixture", deps=deps) as result:
                                        return await result.get_output()
                                return (await agent.run("Read the fixture", deps=deps)).output

                            if enabled:
                                self.assertEqual(await run(), "Done.")
                                self.assertEqual(len(requests), 2)
                                call = next(m for m in requests[1]["messages"] if m.get("tool_calls"))
                                self.assertEqual(json.loads(call["tool_calls"][0]["function"]["arguments"])["resource"], resource)
                                result = next(m for m in requests[1]["messages"] if m["role"] == "tool")
                                self.assertIn("Verified read.", result["content"])
                            else:
                                with self.assertRaises(UnexpectedModelBehavior):
                                    await run()
                                self.assertEqual(len(requests), 1)
