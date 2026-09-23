"""PTC diagnostic limits and real Monty call attribution."""
from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
from types import SimpleNamespace

from pydantic_ai import Agent, FunctionToolset
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.messages import ModelMessagesTypeAdapter, ModelRequest

from deep_agent.agent.context.compaction import _formatted_history
from deep_agent.agent.context.estimator import _history_fingerprint
from deep_agent.agent.deps import AgentDeps
from deep_agent.ptc import FilePayloadResolvingToolset, HybridCodeMode
from deep_agent.ptc.diagnostics import Collector, MAX_DIAGNOSTICS_BYTES, summary
from deep_agent.ptc.file_payload import build_code_mode_tool_package


class PtcDiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def _run(self, snippets: list[str], *, tool=None):
        index = 0
        requests = []

        def respond(messages, _info):
            nonlocal index
            requests.append(messages)
            if index < len(snippets):
                call = ToolCallPart("run_code", {"code": snippets[index]}, f"ptc-{index + 1}")
                index += 1
                return ModelResponse(parts=[call])
            return ModelResponse(parts=[TextPart("done")])

        async def query(value: int) -> dict:
            await asyncio.sleep(0.001 if value % 2 else 0.002)
            return {"value": value, "rows": [value] * 3}

        wrapped = FilePayloadResolvingToolset(FunctionToolset(tools=[tool or query]).prefixed("demo"))
        agent = Agent(
            FunctionModel(respond), deps_type=AgentDeps,
            toolsets=[build_code_mode_tool_package().toolset, wrapped],
            capabilities=[HybridCodeMode(tools=[], dynamic_catalog=True)],
        )
        deps = AgentDeps(session_id="diagnostic-session", run_id="diagnostic-run")
        result = await agent.run("synthetic PTC", deps=deps)
        returns = [part for message in result.new_messages() for part in message.parts
                   if isinstance(part, ToolReturnPart) and part.tool_name == "run_code"]
        return result, returns, requests

    async def test_real_monty_serial_parallel_loop_and_separate_outer_calls(self):
        snippets = [
            "first = await demo_query(value=1)\nsecond = await demo_query(value=2)\nsecond['value']",
            "import asyncio\na, b = await asyncio.gather(demo_query(value=3), demo_query(value=4))\n[a['value'], b['value']]",
            "items = []\nfor i in range(5, 8):\n    items.append(await demo_query(value=i))\nlen(items)",
        ]
        result, returns, requests = await self._run(snippets)
        self.assertEqual(result.output, "done")
        self.assertEqual([len(item.metadata["ptc_diagnostics"]["steps"]) for item in returns], [2, 2, 3])
        self.assertEqual([[step["call_id"] for step in item.metadata["ptc_diagnostics"]["steps"]] for item in returns], [
            ["ptc-1__1", "ptc-1__2"], ["ptc-2__1", "ptc-2__2"], ["ptc-3__1", "ptc-3__2", "ptc-3__3"],
        ])
        parallel = returns[1].metadata["ptc_diagnostics"]
        self.assertLessEqual(parallel["summary"]["external_coverage_ms"], sum(step["duration_ms"] for step in parallel["steps"]))
        self.assertTrue(all("nested_calls" not in item.metadata for item in returns))
        self.assertNotIn("ptc_diagnostics", _formatted_history(result.new_messages()))
        without_diagnostics = [replace(message, parts=[
            replace(part, metadata={}) if isinstance(part, ToolReturnPart) else part
            for part in message.parts
        ]) for message in result.new_messages()]
        self.assertEqual(_history_fingerprint(result.new_messages()), _history_fingerprint(without_diagnostics))
        self.assertGreaterEqual(len(requests), 4)

    def test_bounded_sanitizer_redacts_without_stringifying_unknown_objects(self):
        class Explosive:
            def __str__(self):
                raise AssertionError("must not stringify")

        projected = summary({
            "api_key": "synthetic-private", "file_content": "secret file body",
            "body": "甲" * 10_000, "binary": b"private-bytes", "unknown": Explosive(),
            "array": list(range(100)),
        })
        encoded = json.dumps(projected, ensure_ascii=False)
        self.assertNotIn("synthetic-private", encoded)
        self.assertNotIn("secret file body", encoded)
        self.assertNotIn("private-bytes", encoded)
        self.assertLessEqual(len(encoded.encode()), 8 * 1024)
        self.assertEqual(projected["value"]["unknown"]["type"], "Explosive")

    def test_total_budget_preserves_step_identity(self):
        collector = Collector("outer", "run", "session", frozenset())
        for index in range(100):
            call_id = f"outer__{index + 1}"
            collector.begin(call_id, "synthetic_tool", {"value": "x" * 20_000, "index": index})
            collector.end(call_id, "completed", {"body": "y" * 20_000})
        collector.finish("completed")
        document = collector.document()
        self.assertEqual(len(document["steps"]), 100)
        self.assertEqual(document["summary"]["call_count"], 100)
        self.assertLessEqual(len(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()), MAX_DIAGNOSTICS_BYTES)

    def test_multibyte_identifiers_still_fit_total_budget(self):
        collector = Collector("外" * 1000, "轮" * 1000, "会" * 1000, frozenset())
        for index in range(100):
            call_id = f"{index}-" + "调" * 1000
            collector.begin(call_id, "工具" * 1000, {"value": index})
            collector.end(call_id, "completed", {"value": index})
        collector.finish("completed")
        document = collector.document()
        self.assertEqual(len(document["steps"]), 100)
        self.assertLessEqual(len(json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode()), MAX_DIAGNOSTICS_BYTES)

    def test_redacted_base64_has_redacted_state(self):
        projected = summary("a" * 4000)
        self.assertEqual(projected["state"], "redacted")
        self.assertNotIn("a" * 256, json.dumps(projected))

    def test_official_result_preserves_failure(self):
        collector = Collector("outer", "run", "session", frozenset())
        collector.begin("outer__1", "demo", {})
        collector.end("outer__1", "failed", error={"type": "ToolFailed"})
        collector.execution = SimpleNamespace(
            nested_calls={"outer__1": SimpleNamespace(tool_name="demo", args={})},
            nested_returns={"outer__1": SimpleNamespace(outcome="failed", content="failed")},
        )
        collector.finish("failed")
        step = collector.document()["steps"][0]
        self.assertEqual(step["status"], "failed")
        self.assertEqual(collector.document()["summary"]["failed_count"], 1)

    def test_custom_container_is_opaque_without_invoking_methods(self):
        class BrokenDict(dict):
            def items(self):
                raise RuntimeError("diagnostic projection failed")

        collector = Collector("outer", "run", "session", frozenset())
        collector.begin("outer__1", "demo", BrokenDict(value=1))
        collector.end("outer__1", "completed", {"value": 1})
        collector.finish("completed")
        document = collector.safe_document()
        self.assertEqual(document["summary"]["status"], "completed")
        self.assertEqual(document["completeness"], "complete")
        self.assertEqual(document["steps"][0]["input"]["state"], "redacted")
        self.assertFalse(document["truncation"].get("capture_error", False))

    async def test_diagnostic_marker_never_enters_provider_wire_or_compaction(self):
        marker = "diagnostic-only-marker-73d29"
        request = ModelRequest(parts=[ToolReturnPart(
            "run_code", {"answer": "visible"}, "ptc-call",
            metadata={"code_mode": True, "ptc_diagnostics": {"version": 1, "marker": marker}},
        )])
        model = OpenAIChatModel("synthetic", provider=OpenAIProvider(
            api_key="synthetic-key", base_url="http://127.0.0.1:1",
        ))
        params = ModelRequestParameters()
        current = await model._map_messages([request], params)
        restored = ModelMessagesTypeAdapter.validate_json(ModelMessagesTypeAdapter.dump_json([request]))
        continued = await model._map_messages(restored, params)
        for payload in (current, continued):
            encoded = json.dumps(payload, ensure_ascii=False)
            self.assertIn("visible", encoded)
            self.assertNotIn(marker, encoded)
            self.assertNotIn("ptc_diagnostics", encoded)
        self.assertNotIn(marker, _formatted_history(restored))


if __name__ == "__main__":
    unittest.main()
