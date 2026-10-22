"""Behavioral boundaries: dispatch guards, shared correction budget and resume."""
from __future__ import annotations

import json
import asyncio
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic_ai import Agent, DeferredToolResults, ExternalToolset
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.tools import ToolDefinition
from pydantic_ai.usage import UsageLimits
from sqlalchemy import select

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.structured_output import DISPATCH

# Historical fields are intentionally no longer exported by runtime code.
CONTRACT = "deepagent_output_contract"
CONTRACT_REF = "deepagent_output_contract_ref"
from deep_agent.conversation.projector import project_session_events
from deep_agent.db.models import ChatSessionEvent
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


CONTRACT_DATA = {"name": "lease_result", "description": "frozen synthetic contract",
    "json_schema": {"type": "object", "properties": {"ok": {"type": "boolean"}},
                    "required": ["ok"], "additionalProperties": False}}
CONFIRM = ToolDefinition(name="confirm", parameters_json_schema={"type": "object", "properties": {}})


class StructuredOutputBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="TMP_to_delete_output_boundaries_")
        self.path = Path(self.temp.name) / "db.sqlite"
        self.url = f"sqlite+aiosqlite:///{self.path}"
        self.stores, self.engine, self.factory = await make_sqlite_stores(self.url)
        self.agent_id = await seed_default_agent(self.stores)
        self.persister = EventPersister(self.stores.coordinator)
        await self.persister.start()
        self.title = patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock)
        self.title.start()

    async def asyncTearDown(self):
        self.title.stop()
        await self.persister.aclose()
        await self.engine.dispose()
        self.temp.cleanup()

    def deps(self, sid):
        return AgentDeps(session_id=sid, agent_id=self.agent_id, user="test-user")

    def runner(self, respond, *, loader=None, toolsets=(), request_limit=20):
        async def streaming(messages, info):
            response = respond(messages, info)
            for index, part in enumerate(response.parts):
                if isinstance(part, TextPart):
                    yield part.content
                elif isinstance(part, ToolCallPart):
                    yield {index: DeltaToolCall(name=part.tool_name,
                        json_args=part.args if isinstance(part.args, str) else json.dumps(part.args),
                        tool_call_id=part.tool_call_id)}
        agent = Agent(FunctionModel(respond, stream_function=streaming), deps_type=AgentDeps,
                      toolsets=list(toolsets), retries=2, end_strategy="graceful")
        runner = AgentRunner(agent, self.stores.coordinator, UsageLimits(request_limit=request_limit))
        runner._output_config_loader = loader or AsyncMock(return_value=CONTRACT_DATA)
        return runner, agent

    async def execute(self, runner, sid, *, stream=False, message="synthetic boundary", results=None):
        if stream:
            events = [event async for event in runner.run_stream(sid, message, self.deps(sid),
                deferred_tool_results=results, persister=self.persister)]
            return next((event for event in reversed(events) if event.get("type") == "final"), events[-1]), events
        return await runner.run(sid, message, self.deps(sid), deferred_tool_results=results), []

    async def assert_rejected_history(self, sid, *, expected_attempts=2):
        rows = await self.stores.coordinator.list_all_events(sid)
        items = project_session_events(rows).items
        self.assertFalse([item for item in items if item.kind in {"tool", "plan"}], items)
        attempts = [row for row in rows if row.event_type == "output/attempt"]
        self.assertEqual(len(attempts), expected_attempts)
        self.assertFalse(attempts[0].payload["accepted"])
        self.assertTrue(attempts[-1].payload["accepted"])
        return rows

    async def test_mixed_ordinary_ui_unknown_both_orders_normal_and_stream(self):
        for stream in (False, True):
            for name in ("ordinary_lookup", "confirm", "unknown_tool"):
                for output_first in (False, True):
                    with self.subTest(stream=stream, name=name, output_first=output_first):
                        requests, executed = [], []
                        def respond(messages, info):
                            requests.append(info)
                            output = ToolCallPart("lease_result", {"ok": True}, f"output-{len(requests)}")
                            if len(requests) == 1:
                                other = ToolCallPart(name, {}, "never-dispatched")
                                return ModelResponse(parts=[output, other] if output_first else [other, output])
                            return ModelResponse(parts=[output])
                        runner, agent = self.runner(respond, toolsets=[ExternalToolset([CONFIRM])])
                        @agent.tool_plain
                        async def ordinary_lookup():
                            executed.append(True)
                            return "must not execute"
                        sid = uuid4().hex
                        result, events = await self.execute(runner, sid, stream=stream)
                        self.assertEqual(result.get("output"), {"ok": True}, result)
                        self.assertEqual(len(requests), 2)
                        self.assertFalse(executed)
                        self.assertEqual(requests[0].output_tools, requests[1].output_tools)
                        rows = await self.assert_rejected_history(sid)
                        rejected = [row for row in rows if (((row.payload or {}).get("message") or {}).get("metadata") or {}).get(DISPATCH)]
                        self.assertTrue(rejected, rows)
                        self.assertFalse([event for event in events if event.get("type") == "tool_call"
                                          and event.get("tool_call_id") == "never-dispatched"])
                        from deep_agent.agent.deferred import DeferredResultConflict, validate_deferred_results
                        with self.assertRaises(DeferredResultConflict):
                            await validate_deferred_results(self.stores.coordinator, sid,
                                DeferredToolResults(calls={"never-dispatched": True}))

    async def test_mixed_actual_mcp_both_orders_never_reaches_server_tool(self):
        from tests.integration.helpers.mcp import ECHO_TOOL_NAME, streamable_http_echo_server, url_echo_toolset
        for stream in (False, True):
            for output_first in (False, True):
                with self.subTest(stream=stream, output_first=output_first):
                    record = Path(self.temp.name) / f"mcp-call-{stream}-{output_first}.json"
                    with streamable_http_echo_server(record_path=record) as url:
                        count = 0
                        def respond(messages, info):
                            nonlocal count
                            count += 1
                            output = ToolCallPart("lease_result", {"ok": True}, f"out-{count}")
                            if count == 1:
                                call = ToolCallPart(ECHO_TOOL_NAME, {}, "never-mcp")
                                return ModelResponse(parts=[output, call] if output_first else [call, output])
                            return ModelResponse(parts=[output])
                        runner, _ = self.runner(respond, toolsets=[url_echo_toolset(url)])
                        sid = uuid4().hex
                        result, _ = await self.execute(runner, sid, stream=stream)
                        self.assertEqual(result.get("output"), {"ok": True}, result)
                        self.assertEqual(count, 2)
                        self.assertFalse(record.exists(), "MCP call reached real server")
                        await self.assert_rejected_history(sid)

    async def test_multiple_output_calls_duplicate_and_distinct_ids_are_one_rejection(self):
        for stream in (False, True):
            for duplicate_ids in (False, True):
                with self.subTest(stream=stream, duplicate_ids=duplicate_ids):
                    count = 0
                    def respond(messages, info):
                        nonlocal count
                        count += 1
                        if count == 1:
                            return ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "same"),
                                ToolCallPart("lease_result", {"ok": True}, "same" if duplicate_ids else "other")])
                        return ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "accepted")])
                    runner, _ = self.runner(respond)
                    sid = uuid4().hex
                    result, _ = await self.execute(runner, sid, stream=stream)
                    self.assertEqual(result.get("output"), {"ok": True}, result)
                    self.assertEqual(count, 2)
                    rows = await self.assert_rejected_history(sid)
                    self.assertEqual([r.payload.get("attempt") for r in rows if r.event_type == "output/attempt"], [1, 2])

    async def test_text_json_parse_and_schema_errors_share_only_two_corrections(self):
        for stream in (False, True):
            for order in (("text", "parse", "schema"), ("parse", "schema", "text")):
                with self.subTest(stream=stream, order=order):
                    count = 0
                    def respond(messages, info):
                        nonlocal count
                        count += 1
                        kind = order[min(count - 1, 2)]
                        if kind == "text":
                            return ModelResponse(parts=[TextPart("cannot bypass the output tool")])
                        return ModelResponse(parts=[ToolCallPart("lease_result", "{" if kind == "parse" else {}, f"invalid-{count}")])
                    runner, _ = self.runner(respond)
                    sid = uuid4().hex
                    if stream:
                        result, events = await self.execute(runner, sid, stream=True)
                        self.assertEqual(result.get("type"), "error", result)
                        self.assertFalse([event for event in events if event.get("type") == "output" and event.get("accepted")])
                        self.assertFalse([event for event in events if event.get("type") == "text"])
                    else:
                        with self.assertRaisesRegex(Exception, "output_retry_exhausted"):
                            await self.execute(runner, sid)
                    self.assertEqual(count, 3)
                    rows = await self.stores.coordinator.list_all_events(sid)
                    self.assertEqual(len([row for row in rows if row.event_type == "execution/error"]), 1)
                    self.assertEqual(len([row for row in rows if row.event_type == "output/attempt"]), 3)
                    self.assertFalse([item for item in project_session_events(rows).items if item.kind in {"tool", "structured_output"}])
                    self.assertFalse([item for item in project_session_events(rows).items if item.kind == "assistant_message"])

    async def test_legacy_pending_reloads_current_configuration_without_snapshot(self):
        count = 0
        def respond(messages, info):
            nonlocal count
            count += 1
            if count == 1:
                return ModelResponse(parts=[ToolCallPart("confirm", {}, "legacy-confirm")])
            self.assertFalse(info.output_tools)
            return ModelResponse(parts=[TextPart("legacy final text")])
        runner, _ = self.runner(respond, loader=AsyncMock(return_value=None), toolsets=[ExternalToolset([CONFIRM])])
        sid = uuid4().hex
        pending, _ = await self.execute(runner, sid)
        self.assertEqual(pending["status"], "pending")
        runner._output_config_loader = AsyncMock(return_value=None)
        result, _ = await self.execute(runner, sid, message="", results=DeferredToolResults(calls={"legacy-confirm": True}))
        self.assertEqual(result["output"], "legacy final text")
        self.assertNotIn("output_info", result)
        runner._output_config_loader.assert_awaited_once()
        rows = await self.stores.coordinator.list_all_events(sid)
        self.assertFalse([row for row in rows if row.event_type == "output/attempt"])

    async def test_legacy_snapshot_and_reference_are_ignored_on_fresh_runner_resume(self):
        """Missing, invalid and stale old config cannot block a valid UI answer."""
        from deep_agent.structured_output.schema import schema_digest
        for legacy in ("missing", "valid", "invalid_schema", "wrong_digest", "missing_reference"):
            with self.subTest(legacy=legacy):
                count = 0
                def respond(messages, info):
                    nonlocal count
                    count += 1
                    if count == 1:
                        return ModelResponse(parts=[ToolCallPart("lease_result", {}, "invalid-before-confirm")])
                    if count == 2:
                        return ModelResponse(parts=[ToolCallPart("confirm", {}, "pending-confirm")])
                    self.assertEqual(info.output_tools[0].name, "lease_result")
                    return ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "after-confirm")])
                runner, _ = self.runner(respond, toolsets=[ExternalToolset([CONFIRM])])
                sid = uuid4().hex
                pending, _ = await self.execute(runner, sid)
                self.assertEqual(pending["status"], "pending")
                runner._output_config_loader.assert_awaited_once()
                before = await self.stores.coordinator.list_all_events(sid)
                self.assertFalse(any(CONTRACT in str(row.payload) or CONTRACT_REF in str(row.payload) for row in before))
                legacy_payloads = {}
                if legacy != "missing":
                    async with self.stores.agent._write() as db:
                        rows = list((await db.execute(select(ChatSessionEvent).where(
                            ChatSessionEvent.session_id == sid, ChatSessionEvent.event_type == "user/message"))).scalars())
                        self.assertTrue(rows)
                        row = rows[0]
                        payload = json.loads(json.dumps(row.payload))
                        metadata = dict(payload["message"].get("metadata") or {})
                        payload["message"]["metadata"] = metadata
                        contract = {**CONTRACT_DATA, "schema_digest": schema_digest(CONTRACT_DATA["json_schema"])}
                        if legacy == "invalid_schema":
                            contract["json_schema"] = {"type": "not-a-json-schema-type"}
                        if legacy == "wrong_digest":
                            contract["schema_digest"] = "0" * 64
                        metadata[CONTRACT] = contract
                        if legacy == "missing_reference":
                            metadata[CONTRACT_REF] = {"event_id": "does-not-exist", "schema_digest": "wrong"}
                        row.payload = payload
                        legacy_payloads[row.event_id] = payload
                fresh, _ = self.runner(respond, toolsets=[ExternalToolset([CONFIRM])])
                final, _ = await self.execute(fresh, sid, message="",
                    results=DeferredToolResults(calls={"pending-confirm": True}))
                self.assertEqual(final["output"], {"ok": True})
                self.assertEqual(final["output_info"]["attempt"], 2)
                self.assertEqual(count, 3)
                fresh._output_config_loader.assert_awaited_once()
                rows = await self.stores.coordinator.list_all_events(sid)
                self.assertEqual([r.payload["accepted"] for r in rows if r.event_type == "output/attempt"], [False, True])
                self.assertFalse([i for i in project_session_events(rows).items if i.kind == "tool" and i.status == "awaiting_input"])
                for row in rows:
                    if row.event_id in legacy_payloads:
                        self.assertEqual(row.payload, legacy_payloads[row.event_id], "old config evidence must not be rewritten")
                    elif row.event_id not in {r.event_id for r in before}:
                        self.assertNotIn(CONTRACT, str(row.payload))
                        self.assertNotIn(CONTRACT_REF, str(row.payload))

    async def test_compacted_pending_resumes_in_fresh_process_with_original_budget(self):
        from deep_agent.agent.context.handoff import extract_protected_context, render_handoff_anchor
        for invalid_after_resume, text_first in ((False, False), (True, False), (False, True), (True, True)):
            with self.subTest(invalid_after_resume=invalid_after_resume, text_first=text_first):
                calls = []
                def respond(messages, info):
                    calls.append(info)
                    if text_first and len(calls) == 1:
                        return ModelResponse(parts=[TextPart("missing output consumes the same correction budget")])
                    if len(calls) <= 2:
                        return ModelResponse(parts=[ToolCallPart("lease_result", {}, f"invalid-{len(calls)}")])
                    return ModelResponse(parts=[ToolCallPart("confirm", {}, "frozen-confirm")])
                runner, _ = self.runner(respond, toolsets=[ExternalToolset([CONFIRM])])
                sid = uuid4().hex
                pending, _ = await self.execute(runner, sid)
                self.assertEqual(pending["status"], "pending")
                self.assertEqual(len(calls), 3)
                raw = await self.stores.coordinator.get_raw_history(sid, agent_id=self.agent_id)
                summary = "Two rejected synthetic output candidates; waiting for explicit UI response."
                protected = extract_protected_context(raw, cutoff=len(raw) - 1, raw_history=raw)
                anchor = render_handoff_anchor(summary, protected_context=protected)
                compaction = await self.stores.coordinator.persist_compaction_projection(
                    sid, agent_id=self.agent_id, user_id="test-user", raw_history=raw,
                    projected_history=[anchor, raw[-1]], summary_markdown=summary, trigger="pressure", model_id=None,
                    estimated_before=1000, estimated_after=100, budget_limit_tokens=200,
                    context_window_tokens=4000, preserved_tail_tokens=20, duration_ms=1)
                self.assertIsNotNone(compaction)
                projected = await self.stores.coordinator.get_history(sid, agent_id=self.agent_id)
                self.assertFalse(any(CONTRACT in (message.metadata or {}) for message in projected))
                repo = Path(__file__).resolve().parents[2]
                completed = await asyncio.to_thread(subprocess.run, [sys.executable, "-m",
                    "tests.integration.test_structured_output_boundaries", "--resume-file", str(self.path),
                    "--agent", self.agent_id, "--session", sid,
                    *( ["--invalid"] if invalid_after_resume else [])], cwd=repo,
                    env={**os.environ, "PYTHONPATH": f'{repo / "src"}{os.pathsep}{repo}'},
                    capture_output=True, text=True, timeout=30)
                self.assertEqual(completed.returncode, 0, completed.stderr[-2000:])
                line = next(line for line in completed.stdout.splitlines() if line.startswith("BOUNDARY_RESUME_JSON:"))
                resumed = json.loads(line.removeprefix("BOUNDARY_RESUME_JSON:"))
                self.assertEqual(resumed["model_requests"], 1)
                self.assertEqual(resumed["loader_reads"], 1)
                self.assertEqual(resumed["output_schema"], calls[0].output_tools[0].parameters_json_schema)
                if invalid_after_resume:
                    self.assertIn("output_retry_exhausted", resumed["error"])
                else:
                    self.assertEqual(resumed["result"]["output"], {"ok": True})
                    self.assertEqual(resumed["result"]["output_info"]["attempt"], 3)
                rows = await self.stores.coordinator.list_all_events(sid)
                self.assertEqual(len([row for row in rows if row.event_type == "output/attempt"]), 3)
                accepted = [item for item in project_session_events(rows).items if item.kind == "structured_output"]
                self.assertEqual(len(accepted), 0 if invalid_after_resume else 1)


async def _resume_in_fresh_process(path, agent_id, session_id, invalid):
    stores, engine, _ = await make_sqlite_stores(f"sqlite+aiosqlite:///{path}")
    count = 0
    schema = None
    def response(messages, info):
        nonlocal count, schema
        count += 1
        schema = info.output_tools[0].parameters_json_schema
        return ModelResponse(parts=[ToolCallPart("lease_result", {} if invalid else {"ok": True}, "resumed-output")])
    agent = Agent(FunctionModel(response), deps_type=AgentDeps, toolsets=[ExternalToolset([CONFIRM])], retries=2)
    runner = AgentRunner(agent, stores.coordinator, UsageLimits(request_limit=20))
    runner._output_config_loader = AsyncMock(return_value=CONTRACT_DATA)
    result = None
    error = None
    try:
        with patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock):
            result = await runner.run(session_id, "", AgentDeps(session_id=session_id, agent_id=agent_id, user="test-user"),
                deferred_tool_results=DeferredToolResults(calls={"frozen-confirm": True}))
    except Exception as exc:
        error = str(exc)
    finally:
        await engine.dispose()
    print("BOUNDARY_RESUME_JSON:" + json.dumps({"model_requests": count, "output_schema": schema,
                                               "result": result, "error": error, "loader_reads": runner._output_config_loader.await_count}))


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-file", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--session", required=True)
    parser.add_argument("--invalid", action="store_true")
    args = parser.parse_args()
    asyncio.run(_resume_in_fresh_process(args.resume_file, args.agent, args.session, args.invalid))
