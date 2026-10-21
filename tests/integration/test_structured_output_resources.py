"""Measured platform resource limits, with disposable file SQLite and no network."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import resource
import sys
from time import perf_counter
import tracemalloc
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic_ai import Agent as SdkAgent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.usage import RequestUsage
from sqlalchemy import event, insert

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.structured_output import StructuredOutputState
from deep_agent.capabilities import build_registry
from deep_agent.db.models import Agent
from deep_agent.schemas.agent import AgentUpdate
from deep_agent.security import diagnostic_users
from deep_agent.structured_output.schema import MAX_OUTPUT_BYTES, MAX_SCHEMA_BYTES, json_bytes, normalize_schema, schema_digest
from deep_agent.structured_output.validation import validate_output
from tests.helpers.sqlite_stores import seed_default_agent
from tests.integration import test_agent_packages_api as _fixture
from tests.integration.helpers.chat import mock_request_disconnect

_METRICS = {}

def tearDownModule():
    report = os.environ.get("DEEP_AGENT_RESOURCE_REPORT")
    if report:
        target = Path(report)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# 结构化输出资源专项验收\n\n"
            "本机隔离进程实测，SDK FunctionModel、真实文件 SQLite；不作为容量 SLA 或真实 Provider 性能结论。"
            "SQL 计数只记录对应操作窗口，用户批量测试保留真实 resolver，仅替换无网络 fetcher。"
            "并发测试测量父 Python 的 tracemalloc 峰值，RSS 是进程累计高水位；子进程 RSS 单列。\n\n"
            "```json\n" + json.dumps(_METRICS, ensure_ascii=False, indent=2) + "\n```\n\n"
            "缓存上限另由 tests.unit.test_structured_output_schema 的128项边界回归验证；"
            "本专项不重复将该单元结果计为新的性能测量。\n", encoding="utf-8")


class StructuredOutputResourceTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = _fixture.AgentPackagesApiTests.asyncSetUp
    asyncTearDown = _fixture.AgentPackagesApiTests.asyncTearDown

    def sql_capture(self):
        statements = []
        def capture(conn, cursor, statement, parameters, context, executemany):
            statements.append(statement)
        return statements, capture

    async def test_disabled_zero_config_reads_enabled_one(self):
        self.ctx.registry = build_registry()
        aid = await seed_default_agent(self.stores)
        measurements = []
        for enabled in (False, True):
            if enabled:
                await self.stores.agent.update_unique(aid, AgentUpdate(structured_output_enabled=True,
                    output_config={"name": "resource_result", "json_schema": {"type": "object"}}), user_id="resource-operator")
            def response(messages, info):
                return ModelResponse(parts=[ToolCallPart("resource_result", {"ok": True}, "resource-output")] if enabled else [TextPart("ordinary")])
            statements, capture = self.sql_capture()
            event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
            started = perf_counter()
            try:
                with patch("deep_agent.agent.factory._resolve_model", return_value=FunctionModel(response)), \
                     patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock), mock_request_disconnect():
                    result = await self.client.post("/api/v1/chat", headers=self.headers, json={
                        "agent_id": aid, "message": "Synthetic resource fixture", "capabilities": []})
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
            self.assertEqual(result.status_code, 200, result.text)
            reads = sum("agent_output_configs" in statement.lower() and statement.lstrip().lower().startswith("select") for statement in statements)
            self.assertEqual(reads, int(enabled))
            measurements.append({"enabled": enabled, "output_config_selects": reads, "total_sql": len(statements), "elapsed_ms": round((perf_counter()-started)*1000, 2)})
        _METRICS["runtime_configuration_reads"] = measurements

    async def test_agent_list_10_100_1000_has_constant_sql_and_real_username_batches(self):
        measurements, seeded = [], 0
        for count in (10, 100, 1000):
            async with self.ctx.db_session_factory() as db:
                await db.execute(insert(Agent), [{"id": str(uuid4()), "name": f"resource-{index:04}", "prompt": "fixture", "capabilities": [], "skills": [], "ui_tool_selection": {}, "updated_by": f"user-{index:04}", "structured_output_enabled": bool(index % 2)} for index in range(seeded, count)])
                await db.commit()
            seeded = count
            batches = []
            async def fetch(ids, headers):
                batches.append(ids)
                return {user: {"id": user, "username": "username-"+user, "resolution": "resolved"} for user in ids}
            resolver = diagnostic_users.DiagnosticUserProfileResolver(fetcher=fetch, cache_ttl_seconds=0)
            statements, capture = self.sql_capture()
            event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
            started = perf_counter()
            try:
                with patch.object(diagnostic_users, "_RESOLVER", resolver):
                    response = await self.client.get("/api/v1/agents", headers=self.headers)
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
            self.assertEqual(response.status_code, 200, response.text[:200])
            rows = response.json()
            self.assertEqual(len(rows), count)
            self.assertEqual(len(batches), math.ceil(count / 100))
            self.assertTrue(all(len(batch) <= 100 for batch in batches))
            self.assertTrue(all(row["updated_by_user"]["username"] == "username-"+row["updated_by"] for row in rows))
            self.assertFalse(any("agent_output_configs" in statement.lower() for statement in statements))
            self.assertEqual(len(statements), 1)
            measurements.append({"agents": count, "sql": len(statements), "output_config_selects": 0, "username_fetch_batches": [len(batch) for batch in batches], "elapsed_ms": round((perf_counter()-started)*1000, 2)})
        _METRICS["agent_list_scaling"] = measurements

    async def test_near_limits_concurrent_validation_and_immutable_event_write_measurements(self):
        aid = await seed_default_agent(self.stores)
        schema = {"type": "object", "properties": {"blob": {"type": "string"}}, "required": ["blob"], "description": ""}
        schema["description"] = "s" * (MAX_SCHEMA_BYTES - len(json_bytes(schema)) - 128)
        schema = normalize_schema(schema)
        value = {"blob": "x" * (MAX_OUTPUT_BYTES - len(json_bytes({"blob": ""})) - 128)}
        digest, heartbeats, done = schema_digest(schema), [], asyncio.Event()
        async def heartbeat():
            while not done.is_set():
                heartbeats.append(perf_counter())
                await asyncio.sleep(.005)
        async def operation(index):
            sid, run = uuid4().hex, uuid4().hex
            started = perf_counter()
            self.assertEqual(await validate_output(schema, value), [])
            validated = perf_counter()
            metadata = {"deepagent_message_ordinal": 1, "deepagent_output": {"name": "resource_result", "schema_digest": digest, "part_indices": [0]}}
            messages = [ModelRequest(parts=[UserPromptPart("Synthetic resource input")], metadata={"deepagent_message_ordinal": 0, "deepagent_output_contract": {"name": "resource_result", "json_schema": schema, "schema_digest": digest}}),
                        ModelResponse(parts=[ToolCallPart("resource_result", value, f"call-{index}")], metadata=metadata),
                        ModelRequest(parts=[ToolReturnPart("resource_result", "Final result processed.", f"call-{index}")], metadata={"deepagent_message_ordinal": 2})]
            await self.stores.coordinator.append_message_parts(sid, messages=messages, agent_id=aid,
                user_id="resource-owner", turn_id=run, run_id=run, first_message_ordinal=0, terminal_status="completed")
            row = await self.stores.coordinator.append_output_attempt(sid, agent_id=aid, user_id="resource-owner", turn_id=run, run_id=run,
                payload={"name": "resource_result", "tool_call_id": f"call-{index}", "attempt": 1, "accepted": True, "schema_digest": digest,
                         "source": {"message_ordinal": 1, "part_index": 0}, "output": value})
            self.assertEqual(len(json_bytes(row.payload["output"])), len(json_bytes(value)))
            return {"validation_ms": round((validated-started)*1000, 2), "event_write_ms": round((perf_counter()-validated)*1000, 2)}
        pulse = asyncio.create_task(heartbeat())
        tracemalloc.start()
        started = perf_counter()
        try:
            results = await asyncio.gather(*(operation(index) for index in range(4)))
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            done.set()
            await pulse
        gap = max((b-a for a,b in zip(heartbeats, heartbeats[1:])), default=0)
        self.assertGreater(len(heartbeats), 3)
        self.assertLess(gap, 2.0, "API event loop blocked beyond validation deadline")
        rss_factor = 1 if sys.platform == "darwin" else 1024
        _METRICS["near_limit_concurrency"] = {"schema_bytes": len(json_bytes(schema)), "output_bytes": len(json_bytes(value)), "concurrency": 4,
            "tasks": results, "total_ms": round((perf_counter()-started)*1000, 2), "heartbeat_samples": len(heartbeats), "max_heartbeat_gap_ms": round(gap*1000, 2),
            "python_tracemalloc_peak_bytes": peak, "parent_rss_high_water_bytes": int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss*rss_factor),
            "worker_rss_high_water_bytes": int(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss*rss_factor)}

    async def test_sdk_output_schema_is_budgeted_and_schema_change_invalidates_usage_anchor(self):
        captured = []
        contract = {"name": "resource_result", "json_schema": {"type": "object", "properties": {"text": {"type": "string", "description": "definition budget " * 200}}, "required": ["text"]}}
        state = StructuredOutputState(contract)
        def response(messages, info):
            captured.append(info)
            return ModelResponse(parts=[ToolCallPart("resource_result", {"text": "synthetic"}, "resource-call")])
        await SdkAgent(FunctionModel(response), output_type=state.output_types).run("synthetic")
        self.assertEqual(captured[0].output_tools[0].parameters_json_schema["properties"]["text"]["description"], contract["json_schema"]["properties"]["text"]["description"])
        estimator = TokenEstimator.for_model("synthetic-resource", provider_type="test")
        parameters = ModelRequestParameters(output_tools=captured[0].output_tools)
        without = estimator.request_parameters(ModelRequestParameters())
        included = estimator.request_parameters(parameters)
        self.assertGreater(included, without + 100)
        request = ModelRequest(parts=[UserPromptPart("synthetic")])
        response = ModelResponse(parts=[TextPart("synthetic")], usage=RequestUsage(input_tokens=10000, output_tokens=50))
        estimator.record_usage_anchor([request], parameters, response)
        self.assertEqual(estimator.estimate_request([request,response], parameters).source, "provider_usage_plus_estimate")
        changed = deepcopy(parameters)
        changed.output_tools[0].parameters_json_schema["properties"]["text"]["description"] += "changed"
        estimate = estimator.estimate_request([request,response], changed)
        self.assertEqual(estimate.fallback_reason, "request_parameters_changed")
        _METRICS["schema_budget"] = {"output_tool_count": len(captured[0].output_tools), "without_output_tokens": without, "with_output_tokens": included, "changed_schema_fallback": estimate.fallback_reason}
