from __future__ import annotations

import copy
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import httpx2
from jsonschema import Draft202012Validator
from pydantic_ai import Agent, Tool
from pydantic_ai.messages import ModelRequest, ToolCallPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.profiles import InlineDefsJsonSchemaTransformer
from pydantic_ai.providers.alibaba import AlibabaProvider
from pydantic_ai.tools import ToolDefinition

from deep_agent.models.provider_registry import ProviderRuntimeConfig, resolve_provider_model
from deep_agent.models.qwen import QwenNullableJsonSchemaTransformer


def _model(name: str, provider_type: str = "alibaba", *, video: bool = False):
    return resolve_provider_model(
        model_name=name,
        provider=ProviderRuntimeConfig(
            id="schema-probe",
            name="schema-probe",
            provider_type=provider_type,
            use_default_base_url=False,
            base_url="https://schema-probe.invalid/v1",
            api_key="test-placeholder",
        ),
        input_modalities=["text", "video"] if video else ["text"],
    )


class QwenSchemaRequestTests(unittest.TestCase):
    def test_qwen38_nullable_fields_are_normalized_before_model_request(self):
        schema = {
            "type": "object",
            "properties": {
                "labels": {"type": ["null", "array"], "items": {"type": "string"}},
                "count": {"type": ["integer", "null"], "minimum": 0},
                "enabled": {"type": ["null", "boolean"]},
            },
            "required": ["labels"],
            "additionalProperties": False,
        }
        original = copy.deepcopy(schema)
        for name in ("qwen3.8-flash", "qwen3.8-max"):
            for video in (False, True):
                with self.subTest(model=name, video=video):
                    tool = ToolDefinition(name="batch_probe", parameters_json_schema=schema)
                    params = ModelRequestParameters(function_tools=[tool])
                    _, prepared = _model(name, video=video).prepare_request(None, params)
                    sent = prepared.function_tools[0].parameters_json_schema
                    self.assertIn("anyOf", sent["properties"]["labels"])
                    self.assertIn("anyOf", sent["properties"]["count"])
                    self.assertIn("anyOf", sent["properties"]["enabled"])
                    self.assertEqual(sent["required"], ["labels"])
                    self.assertEqual(schema, original)
                    self.assertEqual(tool.parameters_json_schema, original)

    def test_other_models_and_providers_keep_their_profiles(self):
        for name in ("kimi-k3", "glm-5.2", "deepseek-v4-flash-0731", "qwen3.5-plus", "qwen3.9-flash"):
            with self.subTest(model=name):
                model = _model(name)
                self.assertIs(model.profile["json_schema_transformer"], InlineDefsJsonSchemaTransformer)
        for provider in ("openai", "openai_compatible", "deepseek", "moonshotai"):
            with self.subTest(provider=provider):
                self.assertIsNot(_model("qwen3.8-flash", provider).profile["json_schema_transformer"],
                                 QwenNullableJsonSchemaTransformer)

    def test_qwen_profile_preserves_all_other_sdk_flags(self):
        for name in ("qwen3.8-flash", "qwen3.8-max", "qwen3.8-omni-flash"):
            original = dict(AlibabaProvider.model_profile(name))
            model = _model(name)
            for key, value in original.items():
                if key != "json_schema_transformer":
                    with self.subTest(model=name, flag=key):
                        self.assertEqual(model.profile[key], value)

    def test_output_tools_use_same_adapter_and_keep_original_validation(self):
        from jsonschema import ValidationError, validate
        schema = {"type": "object", "properties": {"labels": {
            "type": ["array", "null"], "items": {"type": "string"}}}, "required": ["labels"]}
        before = copy.deepcopy(schema)
        tool = ToolDefinition(name="output_probe", parameters_json_schema=schema)
        params = ModelRequestParameters(output_tools=[tool], output_mode="tool", allow_text_output=False)
        _, prepared = _model("qwen3.8-flash").prepare_request(None, params)
        self.assertIn("anyOf", prepared.output_tools[0].parameters_json_schema["properties"]["labels"])
        self.assertFalse(prepared.allow_text_output)
        self.assertEqual(tool.parameters_json_schema, before)
        for value in ([], ["a"], None):
            validate({"labels": value}, schema)
        with self.assertRaises(ValidationError):
            validate({"labels": '["a"]'}, schema)


class QwenSchemaEquivalenceTests(unittest.TestCase):
    def assert_equivalent(self, original, values):
        before = copy.deepcopy(original)
        transformed = QwenNullableJsonSchemaTransformer(original).walk()
        Draft202012Validator.check_schema(transformed)
        self.assertEqual(original, before)
        self.assertEqual(QwenNullableJsonSchemaTransformer(original).walk(), transformed)
        self.assertEqual(QwenNullableJsonSchemaTransformer(transformed).walk(), transformed)
        old, new = Draft202012Validator(original), Draft202012Validator(transformed)
        for value in values:
            with self.subTest(schema=original, value=value):
                self.assertEqual(old.is_valid(value), new.is_valid(value))
        return transformed

    def test_all_simple_nullable_types_retain_constraints_and_null_semantics(self):
        cases = [
            {"type": ["null", "array"], "items": {"type": "integer", "minimum": 0},
             "minItems": 1, "maxItems": 2, "uniqueItems": True},
            {"type": ["object", "null"], "properties": {"x": {"type": "integer"}},
             "required": ["x"], "additionalProperties": False},
            {"type": ["null", "string"], "minLength": 1, "maxLength": 3, "pattern": "^a+$"},
            {"type": ["integer", "null"], "minimum": 0, "exclusiveMaximum": 3},
            {"type": ["null", "number"], "multipleOf": 0.5, "exclusiveMinimum": 0},
            {"type": ["boolean", "null"]},
        ]
        values = [None, False, True, 0, 1, 2, 3, -1, 0.5, 0.25, "", "a", "aaa", "aaaa", "b",
                  "null", [], [0], [0, 1], [0, 0], [-1], [0, 1, 2], {}, {"x": 1}, {"x": "1"}, {"x": 1, "y": 2}]
        for schema in cases:
            result = self.assert_equivalent(schema, values)
            self.assertIn("anyOf", result)

    def test_enum_and_const_outside_union_do_not_accidentally_allow_null(self):
        for constraint in ({"enum": [1, 2]}, {"enum": [None, 1]}, {"const": 1}, {"const": None}):
            schema = {"type": ["integer", "null"], "default": None, **constraint}
            transformed = self.assert_equivalent(schema, [None, 0, 1, 2, False, "1"])
            for key, value in constraint.items():
                self.assertEqual(transformed[key], value)
            self.assertIsNone(transformed["default"])

    def test_nested_nullable_objects_arrays_and_references_keep_omission_distinct(self):
        schema = {
            "type": "object",
            "$defs": {"Labels": {"type": ["null", "array"], "items": {"type": "string"}, "minItems": 1}},
            "properties": {
                "data": {"type": ["object", "null"], "properties": {
                    "labels": {"$ref": "#/$defs/Labels", "description": "Field-specific label", "default": None}},
                    "required": ["labels"], "additionalProperties": False},
                "optional": {"type": ["null", "boolean"], "default": False},
            },
            "required": ["data"], "additionalProperties": False,
        }
        result = self.assert_equivalent(schema, [
            {}, {"data": None}, {"data": {}}, {"data": {"labels": None}},
            {"data": {"labels": []}}, {"data": {"labels": ["x"]}},
            {"data": {"labels": [1]}}, {"data": {"labels": ["x"], "extra": 1}},
            {"data": None, "optional": False}, {"data": None, "optional": None},
            {"data": None, "optional": "false"}, {"data": None, "extra": 1},
        ])
        labels = result["properties"]["data"]["anyOf"][0]["properties"]["labels"]
        self.assertEqual(labels["description"], "Field-specific label")
        self.assertIsNone(labels["default"])
        self.assertNotIn("$ref", labels)

    def test_existing_compositions_and_multi_type_unions_are_not_rewritten(self):
        cases = [
            {"anyOf": [{"type": "integer"}, {"type": "null"}]},
            {"type": ["integer", "string", "null"]},
            {"type": ["integer", "null"], "anyOf": [{"minimum": 0}, {"const": None}]},
            {"type": ["integer", "null"], "allOf": [{"not": {"const": 1}}]},
            {"type": ["integer", "null"], "if": {"type": "integer"}, "then": {"minimum": 0}},
            {"type": "array", "items": {"type": "integer"}},
        ]
        for schema in cases:
            result = self.assert_equivalent(schema, [None, False, 0, 1, -1, "x", [], [1]])
            self.assertEqual(result, schema)

    def test_schema_shaped_default_enum_and_examples_remain_literal_data(self):
        literal = {"type": ["null", "integer"], "properties": {"x": {"type": ["array", "null"]}}}
        schema = {"type": ["null", "object"], "default": literal, "enum": [literal, None],
                  "examples": [literal], "description": "Keep the literal data."}
        result = self.assert_equivalent(schema, [None, literal, {}, {"type": None}])
        for key in ("default", "enum", "examples", "description"):
            self.assertEqual(result[key], schema[key])


class QwenSchemaWireTests(unittest.IsolatedAsyncioTestCase):
    async def test_sdk_transports_normalize_tools_for_stream_and_normal_requests(self):
        schema = {"type": "object", "properties": {"labels": {
            "type": ["null", "array"], "items": {"type": "string"}}}, "required": ["labels"]}
        for name in ("qwen3.8-flash", "qwen3.8-max", "kimi-k3", "glm-5.2", "deepseek-v4-flash-0731"):
            for streaming in (False, True):
                calls = []
                def respond(request):
                    self.assertEqual(request.url.host, "schema-probe.invalid")
                    calls.append(json.loads(request.content))
                    fn = {"name": "batch_probe", "arguments": '{"labels":["a"]}'}
                    response = {"id": "probe", "model": name, "created": 1,
                                "choices": [{"index": 0, "message": {"role": "assistant", "content": None,
                                  "tool_calls": [{"id": "c1", "type": "function", "function": fn}]},
                                  "finish_reason": "tool_calls"}]}
                    if not streaming:
                        return httpx2.Response(200, json={"object": "chat.completion", **response})
                    chunks = [{"index": 0, "id": "c1", "type": "function", "function": {
                        "name": "batch_probe", "arguments": '{"labels":'}},
                        {"index": 0, "function": {"arguments": '["a"]}'}}]
                    events = [{"id": "probe", "model": name, "created": 1, "object": "chat.completion.chunk",
                               "choices": [{"index": 0, "delta": {"tool_calls": [chunk]},
                                            "finish_reason": "tool_calls" if i else None}]}
                              for i, chunk in enumerate(chunks)]
                    body = "".join("data: " + json.dumps(e) + "\n\n" for e in events) + "data: [DONE]\n\n"
                    return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=body.encode())
                with self.subTest(model=name, stream=streaming):
                    async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
                        with patch("deep_agent.models.provider_registry.AlibabaProvider",
                                   side_effect=lambda **kw: AlibabaProvider(http_client=client, **kw)):
                            model = _model(name)
                        params = ModelRequestParameters(function_tools=[ToolDefinition(
                            name="batch_probe", parameters_json_schema=schema)])
                        messages = [ModelRequest(parts=[UserPromptPart("Call batch_probe.")])]
                        if streaming:
                            async with model.request_stream(messages, None, params) as stream:
                                async for _ in stream:
                                    pass
                                result = stream.get()
                        else:
                            result = await model.request(messages, None, params)
                    sent = calls[0]["tools"][0]["function"]["parameters"]["properties"]["labels"]
                    self.assertEqual("anyOf" in sent, name.startswith("qwen3.8-"))
                    part = next(p for p in result.parts if isinstance(p, ToolCallPart))
                    self.assertEqual(part.args_as_dict(), {"labels": ["a"]})
                    self.assertEqual(schema["properties"]["labels"]["type"], ["null", "array"])

    async def test_invalid_inbound_array_is_still_retried_without_tool_execution(self):
        executions, requests = [], []
        original_schema = {"type": "object", "properties": {"labels": {
            "type": ["null", "array"], "items": {"type": "string"}}}, "required": ["labels"]}
        async def prepare(ctx, definition):
            return replace(definition, parameters_json_schema=copy.deepcopy(original_schema))
        async def batch_probe(labels: list[str] | None) -> int:
            executions.append(labels)
            return len(labels or [])
        def respond(request):
            requests.append(json.loads(request.content))
            if len(requests) <= 2:
                args = {"labels": '["a"]' if len(requests) == 1 else ["a"]}
                message = {"role": "assistant", "content": None, "tool_calls": [{
                    "id": f"call-{len(requests)}", "type": "function",
                    "function": {"name": "batch_probe", "arguments": json.dumps(args)}}]}
                finish = "tool_calls"
            else:
                message, finish = {"role": "assistant", "content": "done"}, "stop"
            return httpx2.Response(200, json={"id": "probe", "model": "qwen3.8-flash", "created": 1,
                "object": "chat.completion", "choices": [{"index": 0, "message": message, "finish_reason": finish}]})
        async with httpx2.AsyncClient(transport=httpx2.MockTransport(respond)) as client:
            with patch("deep_agent.models.provider_registry.AlibabaProvider",
                       side_effect=lambda **kw: AlibabaProvider(http_client=client, **kw)):
                model = _model("qwen3.8-flash")
            agent = Agent(model, tools=[Tool(batch_probe, prepare=prepare)], retries=1)
            result = await agent.run("Call batch_probe with label a.")
        self.assertEqual(result.output, "done")
        self.assertEqual(executions, [["a"]])
        self.assertEqual(len(requests), 3)
        retry_messages = requests[1]["messages"]
        self.assertTrue(any(m.get("role") == "tool" and "validation" in str(m.get("content")).lower()
                            for m in retry_messages))
        for request in requests:
            self.assertIn("anyOf", request["tools"][0]["function"]["parameters"]["properties"]["labels"])


if __name__ == "__main__":
    unittest.main()
