"""Capture real SDK requests through discovery, Monty, failure and UI resume."""
from __future__ import annotations

from dataclasses import asdict
import unittest

from pydantic_ai import Agent, DeferredToolRequests, DeferredToolResults, ExternalToolset, FunctionToolset
from pydantic_ai.capabilities import ToolSearch
from pydantic_ai.messages import ModelResponse, SystemPromptPart, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.tools import ToolDefinition

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.deps import AgentDeps
from deep_agent.ptc import FilePayloadResolvingToolset, HybridCodeMode
from deep_agent.ptc.file_payload import build_code_mode_tool_package


async def capture_ptc_rule_sequence():
    """One fixed sequence, also used to compare the released source in a process."""
    requests, calls = [], []
    estimator = TokenEstimator.conservative()

    async def alpha() -> str:
        calls.append("alpha")
        return "synthetic alpha"

    async def beta() -> list[int]:
        calls.append("beta")
        return [1, 2, 3]

    def strategy(ctx, queries, tool_defs):
        return [tool.name for tool in tool_defs if any(query in tool.name for query in queries)]

    def respond(messages, info):
        descriptions = {tool.name: tool.description or "" for tool in info.function_tools}
        notices = [part.content for message in messages for part in message.parts
                   if isinstance(part, (SystemPromptPart, UserPromptPart)) and isinstance(part.content, str)
                   and "New tool schemas are available:" in part.content]
        requests.append({"step": len(requests), "descriptions": descriptions,
            "announcements": [notice.removeprefix("<system>").removesuffix("</system>") for notice in notices],
            "raw_announcements": notices, "instructions": info.instructions,
            "estimate": asdict(estimator.estimate_request(messages, info.model_request_parameters))})
        step = len(requests) - 1
        sequence = [
            ("search_tools", {"queries": ["alpha"]}),
            ("search_tools", {"queries": ["alpha"]}),
            ("search_tools", {"queries": ["beta"]}),
            ("demo_alpha", {}),
            ("run_code", {"code": "rows = await demo_beta()\nsum(rows)"}),
            ("run_code", {"code": "await search_tools(queries=['alpha'])"}),
            ("demo_beta", {}),
            ("confirm_fixture", {}),
            ("demo_alpha", {}),
        ]
        if step == len(sequence):
            return ModelResponse(parts=[TextPart("done")])
        assert step < len(sequence), "unexpected extra model request"
        name, args = sequence[step]
        return ModelResponse(parts=[ToolCallPart(name, args, f"fixture-{step}")])

    def build():
        return Agent(FunctionModel(respond), deps_type=AgentDeps,
            toolsets=[build_code_mode_tool_package().toolset,
                FilePayloadResolvingToolset(FunctionToolset(tools=[alpha, beta]).prefixed("demo").defer_loading()),
                ExternalToolset([ToolDefinition(name="confirm_fixture", parameters_json_schema={"type": "object", "properties": {}})])],
            capabilities=[ToolSearch(strategy=strategy), HybridCodeMode(tools=[], dynamic_catalog=True)],
            output_type=[str, DeferredToolRequests])

    pending = await build().run("Synthetic bounded data processing", deps=AgentDeps(session_id="ptc-rule-placement"))
    assert isinstance(pending.output, DeferredToolRequests)
    assert [call.tool_call_id for call in pending.output.calls] == ["fixture-7"]
    before_resume = pending.all_messages()
    # A fresh Agent/capability instance must get the same constraints after resume.
    resumed = await build().run(message_history=before_resume,
        deferred_tool_results=DeferredToolResults(calls={"fixture-7": {"confirmed": True}}),
        deps=AgentDeps(session_id="ptc-rule-placement"))
    assert resumed.output == "done"
    returns = [part for message in resumed.all_messages() for part in message.parts if isinstance(part, ToolReturnPart)]
    ptc = [{"id": part.tool_call_id, "outcome": part.outcome, "content": part.content}
           for part in returns if part.tool_name == "run_code"]
    return {"requests": requests, "native_calls": calls, "ptc": ptc,
        "resumed_output": resumed.output,
        "notices": list(dict.fromkeys(notice for request in requests for notice in request["announcements"]))}


class PTCRulePlacementTests(unittest.IsolatedAsyncioTestCase):
    async def test_rules_live_only_in_run_code_across_discovery_failure_and_fresh_resume(self):
        from deep_agent.ptc.toolset import _USAGE_RULES
        captured = await capture_ptc_rule_sequence()
        self.assertEqual(len(captured["requests"]), 10)
        self.assertEqual(captured["native_calls"], ["alpha", "beta", "beta", "alpha"])
        self.assertEqual([part["outcome"] for part in captured["ptc"]], ["success", "failed"])
        self.assertEqual(captured["ptc"][0]["content"], 6)
        self.assertIn("Prefer ordinary native tools", str(captured["ptc"][1]["content"]))
        self.assertEqual(captured["notices"], ["New tool schemas are available: `demo_alpha`.",
                                              "New tool schemas are available: `demo_beta`."])
        for request in captured["requests"]:
            with self.subTest(step=request["step"]):
                descriptions = request["descriptions"]
                run_code = descriptions["run_code"]
                self.assertIn(_USAGE_RULES, run_code)
                self.assertEqual(run_code.count("DeepAgent PTC rules:"), 1)
                for marker in ("Default to ordinary native MCP calls", "Never use `run_code` to discover",
                    "only inside `run_code`", "authorized session file", "does not roll back earlier nested side effects",
                    "Execute side-effecting tools sequentially", "instead of blindly repeating the write",
                    "Never inspect, measure, print, or return", "cannot directly access host files",
                    "Generic authorized-file relay shape", "Generic public-file relay shape", "Common media-message relay shape"):
                    self.assertIn(marker, run_code)
                search = descriptions.get("search_tools", "")
                self.assertIn("native", search)
                self.assertIn("does not require", search)
                self.assertNotIn("Default to ordinary native MCP calls", search)
                self.assertNotIn("DeepAgent PTC rules:", search)
                self.assertNotIn("DeepAgent PTC rules:", request["instructions"] or "")
                self.assertTrue(all("DeepAgent PTC rules:" not in notice for notice in request["announcements"]))
        self.assertNotIn("demo_alpha", captured["requests"][0]["descriptions"])
        self.assertIn("demo_alpha", captured["requests"][1]["descriptions"])
        self.assertIn("demo_beta", captured["requests"][3]["descriptions"])
        self.assertIn("demo_alpha", captured["requests"][8]["descriptions"])
        self.assertEqual(captured["requests"][8]["announcements"], captured["requests"][7]["announcements"])
