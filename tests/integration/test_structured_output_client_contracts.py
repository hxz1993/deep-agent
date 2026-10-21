"""Platform-only BOM/lease alignment fixtures, not complete customer schemas.

Field names and static combinations follow the three-party design confirmation.
Synthetic business details deliberately do not claim to validate customer state,
real signatures, file access, approvals, submission idempotency or business saves.
"""
from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import AsyncMock
from uuid import uuid4

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.runner import AgentRunner
from deep_agent.conversation.projector import project_session_events
from deep_agent.structured_output.validation import validate_output
from tests.integration import test_structured_output_runtime as runtime_fixture


BOM_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "schema_version": {"const": 3}, "review_mode": {"type": "string"},
        "turn_id": {"type": "string"}, "base_revision": {"type": ["integer", "null"]},
        "base_hash": {"type": ["string", "null"]}, "revision": {"type": "integer"},
        "source_signature": {"type": "string"}, "project": {}, "version": {}, "summary": {},
        "snapshot": {"type": ["object", "null"], "properties": {
            "source_model": {"type": "object"}, "rows": {"type": "array"}},
            "required": ["source_model", "rows"]},
        "patches": {"type": "array", "items": {"type": "object", "properties": {
            "patch_id": {"type": "string"}, "node_key": {"type": "string"},
            "changes": {"type": "object"}, "reason": {"type": "string"}},
            "required": ["patch_id", "node_key", "changes", "reason"]}},
        "question_set": {"type": "null"}, "question_receipts": {"type": "array"},
        "review": {"type": "object"}, "next": {},
    },
    "required": ["schema_version", "turn_id", "base_revision", "base_hash", "revision", "source_signature",
                 "project", "version", "summary", "snapshot", "patches", "question_set", "question_receipts", "review", "next"],
    "allOf": [{"if": {"properties": {"snapshot": {"type": "object"}}, "required": ["snapshot"]},
               "then": {"properties": {"patches": {"maxItems": 0}}}}],
}

BOM_INITIAL = {
    "schema_version": 3, "turn_id": "business-bom-turn-001", "base_revision": 0, "base_hash": None,
    "revision": 1, "source_signature": "synthetic-source-signature", "project": {"id": "fixture-project"},
    "version": {"id": "fixture-version"}, "summary": {"text": "已提取合成来源，等待行确认"},
    "snapshot": {"source_model": {"root": "source-root"},
                 "rows": [{"node_key": "source-row-1", "quantity": 0, "confirmed": False, "unknown": None}]},
    "patches": [], "question_set": None, "question_receipts": [],
    "review": {"row_assessments": [{"node_key": "source-row-1", "issues": ["synthetic issue"]}], "approved": False},
    "next": {"status": "needs_adjustment"},
}
BOM_CONTINUATION = {**copy.deepcopy(BOM_INITIAL), "turn_id": "business-bom-turn-002", "base_revision": 1,
    "base_hash": "business-base-hash", "revision": 2, "snapshot": None,
    "patches": [{"patch_id": "business-patch-001", "node_key": "source-row-1",
                 "changes": {"quantity": 0, "confirmed": False, "unknown": None}, "reason": "用户确认合成行"}]}


def business_object(required, properties=None):
    return {"type": "object", "required": required,
            "properties": {key: {} for key in required} | (properties or {})}


LEASE_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": [],
    "properties": {
        "text": {"type": "string"},
        "lease_contract": business_object(["schema_version", "revision", "title", "summary", "coverage", "issues", "result"]),
        "lease_contract_patch": business_object(["schema_version", "base_revision", "base_hash", "revision", "patch_id", "operations"],
            {"operations": {"type": "array"}}),
        "lease_clarification": business_object(["schema_version", "question_set_id", "revision", "stage", "fields", "receipts"],
            {"fields": {"type": "array"}, "receipts": {"type": "array"}}),
        "lease_review": business_object(["schema_version", "revision", "snapshot_signature", "summary", "findings", "changes"]),
        "lease_review_file": business_object(["file_id", "sha256", "snapshot_signature", "revision"]),
    },
    "allOf": [
        {"not": {"required": ["lease_contract", "lease_contract_patch"]}},
        {"not": {"required": ["lease_review", "lease_review_file"]}},
    ],
}
LEASE_CONTRACT = {"schema_version": "synthetic-lease-v1", "revision": 1, "title": "合成租赁合同", "summary": "仅协议夹具",
    "coverage": [], "issues": [], "result": {"amount": 0, "confirmed": False, "unknown": None},
    "operation_advice": {"fill": {"requires_human_review": True}}}
LEASE_PATCH = {"schema_version": "synthetic-patch-v1", "base_revision": 1, "base_hash": "business-lease-base-hash",
    "revision": 2, "patch_id": "business-lease-patch-001", "operations": [{"op": "replace", "path": "/result/amount", "value": 0}]}
LEASE_CLARIFICATION = {"schema_version": "synthetic-clarification-v1", "question_set_id": "business-question-set-001",
    "revision": 7, "stage": "contract", "fields": [],
    "receipts": [{"submission_id": "business-answer-001", "values": [False, 0, None]}]}
LEASE_REVIEW = {"schema_version": "synthetic-review-v1", "revision": 2, "snapshot_signature": "business-snapshot-signature",
    "summary": "仅展示协议", "findings": [], "changes": []}
LEASE_FILE = {"file_id": "synthetic-private-file", "sha256": "a" * 64,
              "snapshot_signature": "business-snapshot-signature", "revision": 2}


class StructuredOutputClientContractTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = runtime_fixture.StructuredOutputRuntimeTests.asyncSetUp
    asyncTearDown = runtime_fixture.StructuredOutputRuntimeTests.asyncTearDown

    async def run_values(self, name, schema, values, *, stream=False):
        requested, index = [], 0
        def response(messages, info):
            nonlocal index
            requested.append(info)
            value = values[index]
            index += 1
            return ModelResponse(parts=[ToolCallPart(name, value, f"business-output-{index}")])
        async def streaming(messages, info):
            model_response = response(messages, info)
            part = model_response.parts[0]
            yield {0: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args, ensure_ascii=False), tool_call_id=part.tool_call_id)}
        runner = AgentRunner(Agent(FunctionModel(response, stream_function=streaming), deps_type=AgentDeps),
                             self.stores.coordinator, UsageLimits(request_limit=20))
        runner._output_config_loader = AsyncMock(return_value={"name": name, "description": "Synthetic client contract fixture", "json_schema": schema})
        session_id = uuid4().hex
        results = []
        for phase, expected in enumerate(values):
            deps = AgentDeps(session_id=session_id, agent_id=self.agent_id, user="test-user")
            if stream:
                events = [event async for event in runner.run_stream(session_id, f"synthetic business phase {phase}", deps,
                    persister=self.persister)]
                result = next(event for event in reversed(events) if event.get("type") == "final")
            else:
                result = await runner.run(session_id, f"synthetic business phase {phase}", deps)
            self.assertEqual(result["output"], expected)
            self.assertEqual(result["output_info"]["name"], name)
            self.assertEqual(result["status"], "completed")
            results.append(result)
        rows = await self.stores.coordinator.list_all_events(session_id)
        accepted = [item for item in project_session_events(rows).items if item.kind == "structured_output"]
        self.assertEqual([item.output for item in accepted], values)
        self.assertEqual([item.item_id for item in accepted], [result["output_info"]["output_id"] for result in results])
        self.assertTrue(all(info.output_tools == requested[0].output_tools for info in requested))
        return results

    async def test_bom_original_root_static_first_and_continuation_schema(self):
        for stream in (False, True):
            with self.subTest(stream=stream):
                results = await self.run_values("bom_turn_v3", BOM_SCHEMA, [BOM_INITIAL, BOM_CONTINUATION], stream=stream)
                for result, expected in zip(results, (BOM_INITIAL, BOM_CONTINUATION)):
                    self.assertEqual(result["output"]["schema_version"], 3)
                    self.assertNotIn("bom_turn_v3", result["output"])
                    self.assertNotEqual(result["output_info"]["turn_id"], expected["turn_id"])
                    self.assertNotEqual(result["output_info"]["output_id"], expected["turn_id"])
                    self.assertEqual(result["output"]["base_hash"], expected["base_hash"])
                    self.assertIsNone(result["output"]["question_set"])
                    self.assertEqual(result["output"]["question_receipts"], [])
                    self.assertNotIn("deferred", result)
                    self.assertNotIn("submission_id", result["output"])
                    self.assertNotIn("rules_version", result["output"])
                row = results[0]["output"]["snapshot"]["rows"][0]
                self.assertIs(row["confirmed"], False)
                self.assertIs(type(row["quantity"]), int)
                self.assertIsNone(row["unknown"])
        invalid = copy.deepcopy(BOM_INITIAL)
        invalid["snapshot"].pop("source_model")
        self.assertTrue(await validate_output(BOM_SCHEMA, invalid))
        invalid = copy.deepcopy(BOM_INITIAL)
        invalid["patches"] = copy.deepcopy(BOM_CONTINUATION["patches"])
        self.assertTrue(await validate_output(BOM_SCHEMA, invalid))

    async def test_lease_original_names_combinations_optional_and_native_values(self):
        values = [
            {"lease_contract": LEASE_CONTRACT, "lease_clarification": LEASE_CLARIFICATION},
            {"lease_contract_patch": LEASE_PATCH, "lease_clarification": LEASE_CLARIFICATION},
            {"lease_review": LEASE_REVIEW, "lease_contract_patch": LEASE_PATCH},
            {"lease_review_file": LEASE_FILE}, {"text": "没有新增修改"},
            {"lease_clarification": LEASE_CLARIFICATION}, {},
        ]
        for stream in (False, True):
            with self.subTest(stream=stream):
                results = await self.run_values("lease_result", LEASE_SCHEMA, values, stream=stream)
                self.assertEqual(results[0]["output"]["lease_contract"]["result"], {"amount": 0, "confirmed": False, "unknown": None})
                native = results[0]["output"]["lease_contract"]["result"]
                self.assertIs(native["confirmed"], False)
                self.assertIs(type(native["amount"]), int)
                self.assertIsNone(native["unknown"])
                self.assertEqual(results[1]["output"]["lease_clarification"]["receipts"], LEASE_CLARIFICATION["receipts"])
                self.assertEqual(results[-1]["output"], {})
                self.assertEqual(results[3]["output"]["lease_review_file"], LEASE_FILE)
                self.assertEqual(results[0]["output"]["lease_contract"]["schema_version"], "synthetic-lease-v1")
        self.assertTrue(await validate_output(LEASE_SCHEMA, {"lease_contract": None}))

    async def test_lease_static_mutex_is_worker_validated_before_acceptance(self):
        cases = [({"lease_contract": LEASE_CONTRACT, "lease_contract_patch": LEASE_PATCH}, {"lease_contract": LEASE_CONTRACT}),
                 ({"lease_review": LEASE_REVIEW, "lease_review_file": LEASE_FILE}, {"lease_review_file": LEASE_FILE})]
        for invalid, valid in cases:
            self.assertTrue(await validate_output(LEASE_SCHEMA, invalid))
            self.assertEqual(await validate_output(LEASE_SCHEMA, valid), [])
            calls = 0
            def response(messages, info):
                nonlocal calls
                calls += 1
                return ModelResponse(parts=[ToolCallPart("lease_result", invalid if calls == 1 else valid, f"mutex-{calls}")])
            runner = AgentRunner(Agent(FunctionModel(response), deps_type=AgentDeps), self.stores.coordinator,
                                 UsageLimits(request_limit=10))
            runner._output_config_loader = AsyncMock(return_value={"name": "lease_result", "json_schema": LEASE_SCHEMA})
            sid = uuid4().hex
            result = await runner.run(sid, "synthetic static combination", AgentDeps(session_id=sid, agent_id=self.agent_id, user="test-user"))
            self.assertEqual(result["output"], valid)
            self.assertEqual(calls, 2)
            rows = await self.stores.coordinator.list_all_events(sid)
            attempts = [row for row in rows if row.event_type == "output/attempt"]
            self.assertEqual([row.payload["accepted"] for row in attempts], [False, True])
            accepted = [item for item in project_session_events(rows).items if item.kind == "structured_output"]
            self.assertEqual([item.output for item in accepted], [valid])
