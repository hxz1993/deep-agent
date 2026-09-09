from __future__ import annotations

import json
import unittest
from unittest.mock import AsyncMock

from pydantic_ai import Agent, FunctionToolset
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_output_limits import build_tool_output_limits
from deep_agent.ptc import FilePayloadResolvingToolset, HybridCodeMode
from deep_agent.ptc.file_payload import (
    build_code_mode_tool_package, safe_audit_value, safe_result_value,
)


class PTCResultRedactionTests(unittest.TestCase):
    def test_complete_text_rows_fields_and_deep_values_survive(self):
        value = {f"field_{index}": ["文档。" * 2000] * 40 for index in range(40)}
        deep = value
        for _ in range(8):
            deep = {"child": deep}
        self.assertEqual(safe_result_value(deep), deep)
        self.assertNotEqual(safe_audit_value(value), value)

    def test_secrets_and_binary_remain_redacted_at_depth(self):
        text = ("公开正文。" * 1000 + " Bearer syntheticcredential password=syntheticpassword "
                "<extra_identity_context>private identity</extra_identity_context> "
                "deepagent-file-data://abc123 exact-secret " + "A" * 300)
        value = {"text": text, "cookie": "private-cookie", "blob": b"private-bytes"}
        for _ in range(8):
            value = {"child": value}
        projected = safe_result_value(value, sensitive_values=frozenset({"exact-secret"}))
        encoded = json.dumps(projected, ensure_ascii=False)
        for secret in ("syntheticcredential", "syntheticpassword", "private identity",
                       "private-cookie", "private-bytes", "abc123", "exact-secret", "A" * 300):
            self.assertNotIn(secret, encoded)
        self.assertIn("公开正文。" * 1000, encoded)

    def test_cycles_stop_but_repeated_references_are_not_lost(self):
        shared = {"value": "正常正文"}
        root = [shared, shared]
        root.append(root)
        self.assertEqual(safe_result_value(root), [shared, shared, {"type": "list", "circular": True}])

    def test_secret_keys_are_redacted_without_dropping_colliding_values(self):
        result = safe_result_value({
            "private-key-one": "first", "private-key-two": "second",
            "password": "private-value", "A" * 300: "base64-key-value",
        }, sensitive_values=frozenset({"private-key-one", "private-key-two", "password"}))
        encoded = json.dumps(result)
        self.assertNotIn("private-key", encoded)
        self.assertNotIn("private-value", encoded)
        self.assertNotIn("A" * 300, encoded)
        self.assertEqual(len(result), 4)
        self.assertIn("first", result.values())
        self.assertIn("second", result.values())
        self.assertIn("[redacted]", result.values())

    def test_unknown_value_uses_full_string_and_base64_redaction(self):
        class UnknownValue:
            def __init__(self, text):
                self.text = text

            def __str__(self):
                return self.text

        # Short wrapped segments bypass the contiguous-Base64 regex but must
        # still be caught by the whitespace-insensitive whole-value check.
        result = safe_result_value(UnknownValue("\n".join(["A" * 64] * 8)))
        self.assertEqual(result["value"]["type"], "base64")
        self.assertNotIn("A" * 64, json.dumps(result))
        text = "正文。" * 2000 + " synthetic-secret"
        result = safe_result_value(UnknownValue(text), sensitive_values=frozenset({"synthetic-secret"}))
        self.assertEqual(result["value"], "正文。" * 2000 + " [file-payload-redacted]")

    def test_deep_structure_avoids_recursive_walker_limit(self):
        value = "leaf"
        for _ in range(2000):
            value = [value]
        result = safe_result_value(value)
        for _ in range(2000):
            result = result[0]
        self.assertEqual(result, "leaf")


class PTCOutputGovernanceTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, payload, code="await demo_document()"):
        async def document() -> dict:
            return payload

        step = 0

        def respond(messages, info):
            nonlocal step
            step += 1
            if step == 1:
                return ModelResponse(parts=[ToolCallPart(
                    "run_code", {"code": code}, "ptc-doc"
                )])
            return ModelResponse(parts=[TextPart("done")])

        limits, store = build_tool_output_limits(
            request_headers={}, context_window_tokens=1_000_000,
            max_output_tokens=8_000, estimator=TokenEstimator.conservative(),
        )
        deps = AgentDeps(session_id="isolated-ptc-output")
        store.bind(deps, [])
        store.write = AsyncMock(return_value="full-ptc-result")
        agent = Agent(
            FunctionModel(respond), deps_type=AgentDeps,
            toolsets=[build_code_mode_tool_package().toolset,
                      FilePayloadResolvingToolset(FunctionToolset(tools=[document]).prefixed("demo"))],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True), limits],
        )
        result = await agent.run("read document", deps=deps)
        part = next(part for message in result.all_messages() for part in message.parts
                    if isinstance(part, ToolReturnPart) and part.tool_name == "run_code")
        return part, store

    async def test_real_monty_preserves_medium_result_but_bounds_audit(self):
        payload = {"text": "文档内容。" * 2000, "rows": list(range(100))}
        part, store = await self._run(payload)
        self.assertEqual(part.content, payload)
        store.write.assert_not_awaited()
        self.assertLess(len(json.dumps(part.metadata["nested_calls"], ensure_ascii=False)), 4000)

    async def test_real_monty_spills_full_redacted_result_above_48k(self):
        payload = {"text": "文档内容。" * 2000, "password": "synthetic-private-password"}
        part, store = await self._run(payload, (
            "value = await demo_document()\n"
            "{'text': value['text'] * 6, 'password': value['password']}"
        ))
        self.assertEqual(part.metadata["overflow_handle"], "full-ptc-result")
        store.write.assert_awaited_once()
        stored = store.write.await_args.args[1]
        restored = json.loads(stored)
        self.assertEqual(restored["text"], payload["text"] * 6)
        self.assertEqual(restored["password"], "[redacted]")
        self.assertIn("read_tool_result", part.content)
