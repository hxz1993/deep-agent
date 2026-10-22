"""Independent release audit: failed mixed candidates never become live replies."""
from __future__ import annotations

from copy import deepcopy
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from pydantic_ai import Agent, DeferredToolResults, ExternalToolset
from pydantic_ai.messages import ModelResponse, NativeToolCallPart, NativeToolReturnPart, TextPart, ToolCallPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.request_budget import restored_request_count
CONTRACT = "deepagent_output_contract"
from deep_agent.conversation.projector import project_session_events
from tests.helpers.sqlite_stores import make_sqlite_stores
from tests.integration.test_structured_output_boundaries import CONFIRM

from tests.integration import test_allow_text_output_runtime as fixture


class AllowTextOutputExtraBoundaryTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.AllowTextOutputRuntimeTests.asyncSetUp
    asyncTearDown = fixture.AllowTextOutputRuntimeTests.asyncTearDown
    deps = fixture.AllowTextOutputRuntimeTests.deps
    execute = fixture.AllowTextOutputRuntimeTests.execute
    runner = fixture.AllowTextOutputRuntimeTests.runner
    rows = fixture.AllowTextOutputRuntimeTests.rows
    history = fixture.AllowTextOutputRuntimeTests.history

    async def test_rejected_candidate_accompanying_text_is_absent_from_sse_and_history(self):
        for kind in ("schema", "mixed", "multiple"):
            with self.subTest(kind=kind):
                calls, executed = [], []
                async def lookup():
                    executed.append(True)
                    return "must not run"
                def respond(messages, info):
                    calls.append(info)
                    if len(calls) == 1:
                        output = ToolCallPart("lease_result", {} if kind == "schema" else {"ok": True}, "rejected")
                        parts = [TextPart("rejected-candidate-body"), output]
                        if kind == "mixed":
                            parts.append(ToolCallPart("lookup", {}, "never-dispatched"))
                        if kind == "multiple":
                            parts.append(ToolCallPart("lease_result", {"ok": True}, "second-output"))
                        return ModelResponse(parts=parts)
                    return ModelResponse(parts=[TextPart("accepted-final-text")])
                runner = self.runner(respond, loader=AsyncMock(return_value=deepcopy(fixture.BASE)), tools=[lookup])
                sid = uuid4().hex
                final, frames = await self.execute(runner, sid, stream=True)
                self.assertEqual(final["output"], "accepted-final-text")
                self.assertEqual(len(calls), 2)
                self.assertFalse(executed)
                history_text = [item.text for item in await self.history(sid) if item.kind == "assistant_message"]
                self.assertEqual(history_text, ["accepted-final-text"])
                live_text = "".join(frame.get("delta", "") for frame in frames if frame["type"] == "text")
                self.assertNotIn("rejected-candidate-body", live_text)
                self.assertIn("accepted-final-text", live_text)

    async def test_provider_native_search_then_text_is_classified_by_absent_output_call(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                def respond(messages, info):
                    return ModelResponse(parts=[NativeToolCallPart("web_search", {"query": "synthetic"}, "native"),
                        NativeToolReturnPart("web_search", {"hits": []}, "native"), TextPart("native-search-final-text")])
                runner = self.runner(respond, loader=AsyncMock(return_value=deepcopy(fixture.BASE)))
                sid = uuid4().hex
                final, _ = await self.execute(runner, sid, stream=streaming)
                self.assertEqual(final["status"], "completed", final)
                self.assertEqual(final["output"], "native-search-final-text")
                self.assertNotIn("output_info", final)
                self.assertFalse([row for row in await self.rows(sid) if row.event_type == "output/attempt"])

    async def test_compacted_old_strict_and_mixed_pending_resume_in_fresh_process_with_frozen_budgets(self):
        from deep_agent.agent.context.handoff import extract_protected_context, render_handoff_anchor
        for allow_text in (False, True):
            for invalid in (False, True):
                with self.subTest(allow_text=allow_text, invalid=invalid):
                    contract = {key: deepcopy(value) for key, value in fixture.BASE.items()
                                if key != "allow_text_output" or allow_text}
                    requests = []
                    def respond(messages, info):
                        requests.append(info)
                        return ModelResponse(parts=[ToolCallPart("lease_result", {}, f"bad-{len(requests)}")]
                            if len(requests) <= 2 else [ToolCallPart("confirm", {}, "resume-frozen")])
                    runner = self.runner(respond, loader=AsyncMock(return_value=contract), limit=5,
                        toolsets=[ExternalToolset([CONFIRM])])
                    sid = uuid4().hex
                    pending, _ = await self.execute(runner, sid)
                    self.assertEqual(pending["status"], "pending")
                    self.assertEqual(len(requests), 3)
                    raw = await self.stores.coordinator.get_raw_history(sid, agent_id=self.agent_id)
                    summary = "Synthetic waiting UI after two rejected output candidates."
                    protected = extract_protected_context(raw, cutoff=len(raw) - 1, raw_history=raw)
                    anchor = render_handoff_anchor(summary, protected_context=protected)
                    projection = await self.stores.coordinator.persist_compaction_projection(sid,
                        agent_id=self.agent_id, user_id="test-user", raw_history=raw,
                        projected_history=[anchor, raw[-1]], summary_markdown=summary, trigger="pressure", model_id=None,
                        estimated_before=1000, estimated_after=100, budget_limit_tokens=200,
                        context_window_tokens=4000, preserved_tail_tokens=20, duration_ms=1)
                    self.assertIsNotNone(projection)
                    # The persisted compaction summary itself spends one
                    # request, in addition to the three pre-Pending requests.
                    self.assertEqual(restored_request_count(await self.rows(sid)), 4)
                    projected = await self.stores.coordinator.get_history(sid, agent_id=self.agent_id)
                    self.assertFalse(any(CONTRACT in (message.metadata or {}) for message in projected))
                    repo = Path(__file__).resolve().parents[2]
                    process = await asyncio.to_thread(subprocess.run, [sys.executable, "-m",
                        "tests.integration.test_allow_text_output_extra_boundaries", "--resume-extra", str(self.path),
                        "--agent", self.agent_id, "--session", sid, *( ["--allow"] if allow_text else []),
                        *( ["--invalid"] if invalid else [])], cwd=repo,
                        env={**os.environ, "PYTHONPATH": f'{repo / "src"}{os.pathsep}{repo}'},
                        capture_output=True, text=True, timeout=30)
                    self.assertEqual(process.returncode, 0, process.stderr[-2000:])
                    result = json.loads(next(line.removeprefix("FROZEN_EXTRA:") for line in process.stdout.splitlines()
                                             if line.startswith("FROZEN_EXTRA:")))
                    self.assertEqual(result["requests"], 1, result)
                    self.assertEqual(result["allow_text"], allow_text)
                    self.assertFalse(result["function_tools"])
                    self.assertEqual(result["loader_reads"], 1)
                    if invalid:
                        self.assertIn("output_retry_exhausted", result["error"])
                    else:
                        self.assertEqual(result["final"]["output"], {"ok": True})
                        self.assertEqual(result["final"]["output_info"]["attempt"], 3)
                    accepted = [row for row in await self.rows(sid) if row.event_type == "output/attempt" and row.payload["accepted"]]
                    self.assertEqual(len(accepted), 0 if invalid else 1)


async def _resume_extra(path, agent_id, sid, allow_text, invalid):
    stores, engine, _ = await make_sqlite_stores(f"sqlite+aiosqlite:///{path}")
    requests = []
    def respond(messages, info):
        requests.append(info)
        return ModelResponse(parts=[ToolCallPart("lease_result", {} if invalid else {"ok": True}, "after-restart")])
    async def ordinary_probe():
        raise AssertionError("the final budget request must not expose ordinary tools")
    runner = AgentRunner(Agent(FunctionModel(respond), deps_type=AgentDeps, tools=[ordinary_probe],
        toolsets=[ExternalToolset([CONFIRM])], retries=2), stores.coordinator, UsageLimits(request_limit=5))
    loader = AsyncMock(return_value={**deepcopy(fixture.BASE), "allow_text_output": allow_text})
    runner._output_config_loader = loader
    final, error = None, None
    try:
        final = await runner.run(sid, "", AgentDeps(session_id=sid, agent_id=agent_id, user="test-user"),
            deferred_tool_results=DeferredToolResults(calls={"resume-frozen": True}))
    except Exception as exc:
        error = str(exc)
    finally:
        await engine.dispose()
    print("FROZEN_EXTRA:" + json.dumps({"final": final, "error": error, "requests": len(requests),
        "allow_text": requests[0].allow_text_output if requests else None,
        "function_tools": [tool.name for tool in requests[0].function_tools] if requests else [],
        "loader_reads": loader.await_count}))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-extra", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--allow", action="store_true")
    parser.add_argument("--invalid", action="store_true")
    args = parser.parse_args()
    asyncio.run(_resume_extra(args.resume_extra, args.agent, args.session, args.allow, args.invalid))
