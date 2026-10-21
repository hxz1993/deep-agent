"""Immutable output acceptance and failure boundaries, using actual SQLite IO."""
from __future__ import annotations

import asyncio
import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic_ai import Agent
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, event, insert, select

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from deep_agent.agent.planning import PLAN_METADATA_KEY, current_plan_snapshot, restore_plan_snapshot
from deep_agent.agent.structured_output import StructuredOutputState
from deep_agent.conversation.projector import project_session_events
from deep_agent.core.session_live import SessionLiveHub
from deep_agent.db.event_codec import MESSAGE_EVENT_TYPES
from deep_agent.db.models import Agent as AgentRow, ChatSessionEvent
from deep_agent.diagnostics.sessions import DiagnosticSessionQueries, SessionFilters, detail_event_metadata
from deep_agent.diagnostics.usage import UsageQueries
from deep_agent.structured_output.schema import json_bytes, normalize_schema, schema_digest
from deep_agent.structured_output.validation import OutputValidationServiceError, validate_output
from deep_agent.schemas.agent import AgentUpdate
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}, "required": ["ok"]}
DIGEST = schema_digest(SCHEMA)
CONTRACT = {"name": "lease_result", "description": "synthetic", "json_schema": SCHEMA, "schema_digest": DIGEST}


class StructuredOutputPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.stores, self.engine, self.factory = await make_sqlite_stores()
        self.agent_id = await seed_default_agent(self.stores)
        self.store = self.stores.coordinator
        self.sid = uuid4().hex
        self.turn = uuid4().hex
        self.run = uuid4().hex
        self.persister = EventPersister(self.store)
        await self.persister.start()
        self.title = patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock)
        self.title.start()

    async def asyncTearDown(self):
        self.title.stop()
        await self.persister.aclose()
        await self.engine.dispose()

    async def prepare(self, *, acknowledgement=True, ack_first=False, rejected=False, ordinal=1, run_id=None, turn_id=None, sid=None):
        run_id, turn_id, sid = run_id or self.run, turn_id or self.turn, sid or self.sid
        user = ModelRequest(parts=[UserPromptPart("synthetic input")], metadata={"deepagent_message_ordinal": 0, "deepagent_output_contract": CONTRACT})
        metadata = {"deepagent_message_ordinal": ordinal, "deepagent_output": {
            "name": "lease_result", "schema_digest": DIGEST, "part_indices": [0]}}
        if rejected:
            metadata["deepagent_dispatch"] = {"rejected": True, "reason": "mixed_output_calls", "not_dispatched_part_indices": [0]}
        call = ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "same-call-id")], metadata=metadata)
        ack = ModelRequest(parts=[ToolReturnPart("lease_result", "Final result processed.", "same-call-id")], metadata={"deepagent_message_ordinal": ordinal - 1 if ack_first else ordinal + 1})
        messages = [user, *([ack, call] if ack_first else [call, ack])] if acknowledgement else [user, call]
        await self.store.append_message_parts(sid, messages=messages, agent_id=self.agent_id,
            user_id="owner", turn_id=turn_id, run_id=run_id, first_message_ordinal=0, terminal_status="completed")

    def payload(self, *, ordinal=1, part_index=0, **changes):
        return {"name": "lease_result", "tool_call_id": "same-call-id", "attempt": 1,
                "schema_digest": DIGEST, "source": {"message_ordinal": ordinal, "part_index": part_index},
                "accepted": True, "output": {"ok": True}, **changes}

    async def append(self, payload=None, **changes):
        args = dict(agent_id=self.agent_id, user_id="owner", turn_id=self.turn, run_id=self.run)
        args.update(changes)
        return await self.store.append_output_attempt(self.sid, payload=payload or self.payload(), **args)

    async def test_acceptance_preserves_real_call_ack_native_json_and_history_identity(self):
        await self.prepare()
        accepted = await self.append()
        rows = await self.store.list_all_events(self.sid)
        self.assertNotIn("output/attempt", MESSAGE_EVENT_TYPES)
        self.assertEqual(len([row for row in rows if row.event_type == "output/attempt"]), 1)
        self.assertEqual(accepted.status, "completed")
        self.assertEqual(accepted.payload["output"], {"ok": True})
        self.assertTrue(accepted.payload["source"]["event_ids"])
        raw = await self.store.get_history(self.sid, agent_id=self.agent_id)
        self.assertEqual(len(raw), 3)
        history = project_session_events(rows)
        outputs = [item for item in history.items if item.kind == "structured_output"]
        self.assertEqual(len(outputs), 1)
        self.assertEqual(outputs[0].item_id, accepted.event_id)
        self.assertFalse([item for item in history.items if item.kind == "tool"])

    async def test_deterministic_id_is_immutable_and_idempotent(self):
        await self.prepare()
        first = await self.append()
        head = await self.store.get_event_head(self.sid, agent_id=self.agent_id)
        again = await self.append()
        self.assertEqual((again.event_id, again.sequence, again.created_at), (first.event_id, first.sequence, first.created_at))
        self.assertEqual(await self.store.get_event_head(self.sid, agent_id=self.agent_id), head)
        for changes in ({"output": {"ok": False}}, {"attempt": 2}, {"accepted": False, "errors": [{"message": "bad"}]}):
            payload = self.payload(**changes)
            if not payload["accepted"]:
                payload.pop("output")
            with self.subTest(changes=changes), self.assertRaises(ValueError):
                await self.append(payload)
        saved = next(row for row in await self.store.list_all_events(self.sid) if row.event_id == first.event_id)
        self.assertEqual(saved.payload["output"], {"ok": True})

    async def test_same_tool_call_id_in_different_responses_and_runs_does_not_collide(self):
        await self.prepare()
        first = await self.append()
        other_run = uuid4().hex
        await self.prepare(run_id=other_run)
        second = await self.append(run_id=other_run)
        await self.prepare(ordinal=4, run_id=other_run)
        third = await self.append(self.payload(ordinal=4), run_id=other_run)
        self.assertEqual(len({first.event_id, second.event_id, third.event_id}), 3)

    async def test_source_owner_agent_turn_run_ordinal_part_and_call_must_match(self):
        await self.prepare()
        for identity in ({"user_id": "intruder"}, {"agent_id": "wrong-agent"}, {"turn_id": "wrong-turn"}, {"run_id": "wrong-run"}):
            with self.subTest(identity=identity), self.assertRaises((PermissionError, ValueError)):
                await self.append(**identity)
        for payload in (self.payload(ordinal=20), self.payload(part_index=20), self.payload(part_index=-1), self.payload(tool_call_id="wrong-call"), self.payload(schema_digest="0" * 64), self.payload(name="other_result")):
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                await self.append(payload)
        self.assertFalse([row for row in await self.store.list_all_events(self.sid) if row.event_type == "output/attempt"])

    async def test_acknowledgement_must_be_durable_later_and_in_the_same_turn(self):
        await self.prepare(acknowledgement=False)
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            await self.append()
        other_turn = uuid4().hex
        await self.store.append_message_parts(self.sid, messages=[ModelRequest(parts=[ToolReturnPart("lease_result", "ack", "same-call-id")], metadata={"deepagent_message_ordinal": 2})], agent_id=self.agent_id, user_id="owner", turn_id=other_turn, run_id=self.run)
        with self.assertRaisesRegex(ValueError, "acknowledgement"):
            await self.append()

    async def test_rejected_response_cannot_become_an_accepted_result(self):
        await self.prepare(rejected=True)
        with self.assertRaisesRegex(ValueError, "Rejected output"):
            await self.append()

    async def test_commit_failure_does_not_publish_half_an_accepted_fact(self):
        await self.prepare()
        with patch.object(AsyncSession, "commit", side_effect=RuntimeError("injected commit failure")):
            with self.assertRaisesRegex(RuntimeError, "injected"):
                await self.append()
        self.assertFalse([row for row in await self.store.list_all_events(self.sid) if row.event_type == "output/attempt"])
        self.assertFalse([item for item in project_session_events(await self.store.list_all_events(self.sid)).items if item.kind == "structured_output"])

    async def test_commit_acknowledgement_loss_reads_back_committed_identity(self):
        await self.prepare()
        original = AsyncSession.commit
        async def lose_confirmation(db):
            await original(db)
            raise RuntimeError("injected lost commit acknowledgement")
        with patch.object(AsyncSession, "commit", lose_confirmation):
            accepted = await self.append()
        self.assertEqual(accepted.payload["output"], {"ok": True})
        self.assertEqual(len([row for row in await self.store.list_all_events(self.sid) if row.event_type == "output/attempt"]), 1)

    async def test_cancel_before_commit_rolls_back_without_late_acceptance(self):
        await self.prepare()
        reached = asyncio.Event()
        original = AsyncSession.flush
        async def block_output_flush(db, *args, **kwargs):
            if any(getattr(item, "event_type", None) == "output/attempt" for item in db.new):
                reached.set()
                await asyncio.Event().wait()
            return await original(db, *args, **kwargs)
        with patch.object(AsyncSession, "flush", block_output_flush):
            task = asyncio.create_task(self.append())
            await asyncio.wait_for(reached.wait(), 2)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
        await asyncio.sleep(0.02)
        self.assertFalse([row for row in await self.store.list_all_events(self.sid) if row.event_type == "output/attempt"])

    async def test_cancel_at_commit_settles_transaction_before_exposing_result(self):
        await self.prepare()
        reached, release = asyncio.Event(), asyncio.Event()
        original = AsyncSession.commit
        async def gated_commit(db):
            reached.set()
            await release.wait()
            await original(db)
        with patch.object(AsyncSession, "commit", gated_commit):
            task = asyncio.create_task(self.append())
            await asyncio.wait_for(reached.wait(), 2)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            accepted = await asyncio.wait_for(task, 2)
        self.assertEqual(accepted.status, "completed")
        self.assertEqual(len([row for row in await self.store.list_all_events(self.sid) if row.event_type == "output/attempt"]), 1)

    async def test_notification_failure_cannot_reverse_committed_acceptance(self):
        await self.prepare()
        state = StructuredOutputState(CONTRACT)
        state.pending = self.payload()
        state.pending.pop("accepted")
        state.queue = SimpleNamespace(put_nowait=lambda _: (_ for _ in ()).throw(ConnectionError("observer closed")))
        deps = SimpleNamespace(output_store=self.store, session_id=self.sid, agent_id=self.agent_id, user="owner", turn_id=self.turn, run_id=self.run)
        accepted = await state.accept(deps)
        self.assertEqual(state.accepted["output_id"], accepted.event_id)
        self.assertEqual(accepted.status, "completed")

    async def test_semantic_events_do_not_duplicate_usage_queries_or_execution_counts(self):
        await self.prepare()
        before = detail_event_metadata(await self.store.list_all_events(self.sid))
        await self.append()
        after = detail_event_metadata(await self.store.list_all_events(self.sid))
        for key in ("usage", "message_count", "turn_count", "tool_call_count", "attachment_count"):
            self.assertEqual(after[key], before[key], key)
        self.assertEqual(after["tool_call_count"], 0)

    async def test_output_preserves_mixed_plan_context_attachments_and_official_plan_restore(self):
        old_run = uuid4().hex
        deps = AgentDeps(session_id=self.sid, agent_id=self.agent_id, user="owner")
        await restore_plan_snapshot(deps, {"items": [{"id": "read", "content": "读取合成附件", "status": "completed"}]})
        expected_plan = await current_plan_snapshot(deps)
        messages = [
            ModelRequest(parts=[UserPromptPart([
                {"kind": "text-content", "content": "分析合成预算"},
                {"kind": "attachment-ref", "id": "synthetic-file", "name": "合成预算.xlsx", "mime": "application/vnd.ms-excel"},
                {"kind": "context-ref", "key": "range", "type": "deeptable.range", "label": "预算!B2:C4", "metadata": {"token": "SYNTHETIC_PRIVATE_CONTEXT"}},
            ])]),
            ModelResponse(parts=[TextPart("读取中"), ToolCallPart("lookup", {}, "lookup-old"),
                ToolCallPart("write_plan", {"items": expected_plan["items"]}, "plan-old")]),
            ModelRequest(parts=[ToolReturnPart("lookup", {"count": 2}, "lookup-old"),
                ToolReturnPart("write_plan", {"ok": True}, "plan-old", metadata={PLAN_METADATA_KEY: expected_plan})]),
            ModelResponse(parts=[TextPart("原处理已完成")]),
        ]
        await self.store.append_message_parts(self.sid, messages=messages, agent_id=self.agent_id,
            user_id="owner", turn_id=self.turn, run_id=old_run, first_message_ordinal=0, terminal_status="completed")
        before = project_session_events(await self.store.list_all_events(self.sid))
        old_items = {item.item_id: item.model_dump(mode="json") for item in before.items}
        self.assertEqual([item.kind for item in before.items], ["user_message", "assistant_message", "tool", "plan", "assistant_message"])
        user = before.items[0]
        self.assertEqual(user.attachments[0].name, "合成预算.xlsx")
        self.assertEqual(user.context_refs[0]["label"], "预算!B2:C4")
        self.assertNotIn("metadata", user.context_refs[0])
        await self.prepare()
        accepted = await self.append()
        rows = await self.store.list_all_events(self.sid)
        after = project_session_events(rows)
        self.assertEqual(len(after.items), len(old_items) + 2)
        self.assertEqual({item.item_id: item.model_dump(mode="json") for item in after.items if item.item_id in old_items}, old_items)
        self.assertEqual(sum(item.kind == "plan" for item in after.items), 1)
        self.assertEqual([item.item_id for item in after.items if item.kind == "structured_output"], [accepted.event_id])
        saved_plan = await self.store.latest_plan_snapshot(self.sid, self.turn)
        self.assertEqual(saved_plan, expected_plan)
        resumed = AgentDeps(session_id=self.sid, agent_id=self.agent_id, user="owner")
        await restore_plan_snapshot(resumed, saved_plan)
        self.assertEqual(await current_plan_snapshot(resumed), expected_plan)
        self.assertEqual(await self.store.latest_plan_snapshot(self.sid, "other-turn"), None)

    async def test_chat_and_session_title_changes_do_not_update_agent_actor_or_timestamp(self):
        configured = await self.stores.agent.update_unique(self.agent_id, AgentUpdate(structured_output_enabled=True,
            output_config={key: value for key, value in CONTRACT.items() if key != "schema_digest"}), user_id="trusted-config-editor")
        baseline = (configured.updated_by, configured.updated_at)
        def response(messages, info):
            return ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "out-actor")])
        runner = AgentRunner(Agent(FunctionModel(response), deps_type=AgentDeps), self.store, UsageLimits(request_limit=5))
        runner._output_config_loader = AsyncMock(return_value=CONTRACT)
        result = await runner.run(self.sid, "synthetic", AgentDeps(session_id=self.sid, agent_id=self.agent_id, user="owner"))
        self.assertEqual(result["status"], "completed")
        await self.store.apply_session_title(self.sid, agent_id=self.agent_id, user_id="owner", title="合成手工会话标题",
            source="user", source_event_id=None, turn_id=None, run_id=None)
        self.assertEqual(await self.store.get_session_title(self.sid, agent_id=self.agent_id), "合成手工会话标题")
        reread = await self.stores.agent.get_by_id(self.agent_id)
        self.assertEqual((reread.updated_by, reread.updated_at), baseline)

    async def test_snapshot_and_near_limit_output_storage_growth_stays_in_event_store_without_schema_index(self):
        schema = normalize_schema({"type": "object", "description": "SYNTHETIC_PRIVATE_SCHEMA_" + "s" * 62000,
            "properties": {"blob": {"type": "string"}}, "required": ["blob"]})
        output = {"blob": "x" * (1024 * 1024 - 20)}
        contract = {"name": "large_result", "description": "synthetic", "json_schema": schema, "schema_digest": schema_digest(schema)}
        self.assertEqual(await validate_output(schema, output), [])
        metadata = {"deepagent_message_ordinal": 1, "deepagent_output": {
            "name": contract["name"], "schema_digest": contract["schema_digest"], "part_indices": [0]}}
        messages = [ModelRequest(parts=[UserPromptPart("synthetic")], metadata={"deepagent_message_ordinal": 0, "deepagent_output_contract": contract}),
            ModelResponse(parts=[ToolCallPart("large_result", output, "large-call")], metadata=metadata),
            ModelRequest(parts=[ToolReturnPart("large_result", "Final result processed.", "large-call")], metadata={"deepagent_message_ordinal": 2})]
        await self.store.append_message_parts(self.sid, messages=messages, agent_id=self.agent_id,
            user_id="owner", turn_id=self.turn, run_id=self.run, first_message_ordinal=0, terminal_status="completed")
        before = await self.store.list_all_events(self.sid)
        fact = await self.append({"name": "large_result", "tool_call_id": "large-call", "attempt": 1,
            "schema_digest": contract["schema_digest"], "source": {"message_ordinal": 1, "part_index": 0}, "accepted": True, "output": output})
        after = await self.store.list_all_events(self.sid)
        before_bytes = sum(len(json_bytes(row.payload)) for row in before)
        after_bytes = sum(len(json_bytes(row.payload)) for row in after)
        growth = after_bytes - before_bytes
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual(growth, len(json_bytes(fact.payload)))
        self.assertGreater(growth, 1024 * 1024 - 20)
        self.assertLess(growth, 1024 * 1024 + 2048)
        self.assertEqual(sum("SYNTHETIC_PRIVATE_SCHEMA_" in json.dumps(row.payload) for row in after), 1)
        async with self.factory() as db:
            indexed = (await db.execute(select(ChatSessionEvent.event_id, ChatSessionEvent.search_text)
                .where(ChatSessionEvent.session_id == self.sid))).all()
        self.assertFalse(any("SYNTHETIC_PRIVATE_SCHEMA_" in (value or "") for _, value in indexed))
        self.assertIsNone(dict(indexed)[fact.event_id])
        async with self.engine.connect() as connection:
            tables = await connection.exec_driver_sql("SELECT name FROM sqlite_master WHERE type = 'table'")
            output_tables = {name for name in tables.scalars() if "output" in name}
        self.assertEqual(output_tables, {"agent_output_configs"})
        # A compact, credential-free resource record for the acceptance report.
        self.storage_measurement = {"schema_bytes": len(json_bytes(schema)), "output_bytes": len(json_bytes(output)),
            "before_payload_bytes": before_bytes, "after_payload_bytes": after_bytes,
            "semantic_growth_bytes": growth, "before_events": len(before), "after_events": len(after)}

    async def test_legacy_tool_named_like_output_remains_an_ordinary_tool(self):
        await self.store.append(self.sid, [ModelRequest(parts=[UserPromptPart("synthetic legacy")]),
            ModelResponse(parts=[ToolCallPart("lease_result", {"legacy": True}, "legacy-call")]),
            ModelRequest(parts=[ToolReturnPart("lease_result", {"legacy": True}, "legacy-call")])],
            agent_id=self.agent_id, user_id="owner", turn_id=self.turn, run_id=self.run, terminal_status="completed")
        history = project_session_events(await self.store.list_all_events(self.sid))
        self.assertEqual(len([item for item in history.items if item.kind == "tool"]), 1)
        self.assertFalse([item for item in history.items if item.kind == "structured_output"])

    async def test_legacy_fenced_candidate_is_not_accepted_or_formal_history(self):
        candidate = '```lease_result\n{"ok":true}\n```'
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                calls, sid = [], uuid4().hex
                def response(messages, info):
                    calls.append(True)
                    return (ModelResponse(parts=[TextPart(candidate)]) if len(calls) == 1 else
                            ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "accepted")]))
                async def stream(messages, info):
                    result = response(messages, info)
                    for index, part in enumerate(result.parts):
                        if isinstance(part, TextPart):
                            yield part.content
                        else:
                            yield {index: DeltaToolCall(name=part.tool_name, json_args=json.dumps(part.args), tool_call_id=part.tool_call_id)}
                runner = AgentRunner(Agent(FunctionModel(response, stream_function=stream), deps_type=AgentDeps), self.store, UsageLimits(request_limit=5))
                runner._output_config_loader = AsyncMock(return_value=CONTRACT)
                deps = AgentDeps(session_id=sid, agent_id=self.agent_id, user="owner")
                if streaming:
                    frames = [item async for item in runner.run_stream(sid, "synthetic", deps, persister=self.persister)]
                    self.assertEqual(frames[-1]["type"], "final")
                    self.assertFalse([item for item in frames if item["type"] == "text"])
                else:
                    self.assertEqual((await runner.run(sid, "synthetic", deps))["output"], {"ok": True})
                history = project_session_events(await self.store.list_all_events(sid))
                self.assertEqual(len([item for item in history.items if item.kind == "structured_output"]), 1)
                self.assertNotIn("```lease_result", history.model_dump_json())
                self.assertEqual(len(calls), 2)

    async def test_live_overflow_resets_to_whole_accepted_history_without_partial_json(self):
        await self.prepare()
        accepted = await self.append()
        hub = SessionLiveHub(max_queue_frames=1)
        key = (self.agent_id, self.sid, "owner")
        hub.begin(key, self.run, baseline_sequence=accepted.sequence - 1)
        observer = hub.subscribe(key, self.run)
        try:
            hub.publish(key, self.run, "output", {"type": "output", "output_id": accepted.event_id, "output": {"ok": True}})
            hub.publish(key, self.run, "final", {"type": "final", "output": {"ok": True}})
            reset = await observer.get()
            self.assertEqual(reset.event, "observation_reset")
            self.assertEqual(json.loads(reset.data)["reason"], "slow_consumer")
            output = next(item for item in project_session_events(await self.store.list_all_events(self.sid)).items if item.kind == "structured_output")
            self.assertEqual((output.item_id, output.output), (accepted.event_id, {"ok": True}))
        finally:
            hub.close()

    async def test_validator_service_failure_is_not_retried_or_accepted(self):
        requested = []
        def response(messages, info):
            requested.append(True)
            return ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "output-1")])
        runner = AgentRunner(Agent(FunctionModel(response), deps_type=AgentDeps), self.store, UsageLimits(request_limit=10))
        runner._output_config_loader = AsyncMock(return_value=CONTRACT)
        deps = AgentDeps(session_id=self.sid, agent_id=self.agent_id, user="owner")
        with patch("deep_agent.structured_output.validation.validate_output", side_effect=OutputValidationServiceError("validation_timeout")):
            with self.assertRaises(OutputValidationServiceError):
                await runner.run(self.sid, "synthetic", deps)
        rows = await self.store.list_all_events(self.sid)
        self.assertEqual(len(requested), 1)
        self.assertFalse([row for row in rows if row.event_type == "output/attempt"])
        self.assertEqual(len([row for row in rows if row.event_type == "execution/error"]), 1)
        self.assertTrue([row for row in rows if row.event_type == "assistant/message"])

    async def test_sdk_ack_write_failure_never_commits_accepted_output_normal_and_stream(self):
        original = AsyncSession.flush
        async def reject_ack(db, *args, **kwargs):
            for row in db.new:
                if getattr(row, "event_type", None) in {"tool/results", "model/request"}:
                    parts = (row.payload.get("message") or {}).get("parts") or []
                    if any(part.get("part_kind") == "tool-return" and part.get("tool_name") == "lease_result" for part in parts):
                        raise RuntimeError("injected SDK acknowledgement write failure")
            return await original(db, *args, **kwargs)
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                sid = uuid4().hex
                def response(messages, info):
                    return ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "out-1")])
                async def stream(messages, info):
                    yield {0: DeltaToolCall(name="lease_result", json_args='{"ok":true}', tool_call_id="out-1")}
                runner = AgentRunner(Agent(FunctionModel(response, stream_function=stream), deps_type=AgentDeps), self.store, UsageLimits(request_limit=5))
                runner._output_config_loader = AsyncMock(return_value=CONTRACT)
                deps = AgentDeps(session_id=sid, agent_id=self.agent_id, user="owner")
                with patch.object(AsyncSession, "flush", reject_ack):
                    if streaming:
                        frames = [item async for item in runner.run_stream(sid, "synthetic", deps, persister=self.persister)]
                        self.assertNotIn("final", [item["type"] for item in frames])
                        self.assertEqual([item["type"] for item in frames].count("error"), 1)
                    else:
                        with self.assertRaises(Exception):
                            await runner.run(sid, "synthetic", deps)
                rows = await self.store.list_all_events(sid)
                self.assertFalse([row for row in rows if row.event_type == "output/attempt" and row.payload.get("accepted")])
                self.assertFalse([item for item in project_session_events(rows).items if item.kind == "structured_output"])

    async def test_budget_checkpoint_write_failure_stops_before_next_model_request(self):
        from deep_agent.agent.message_persistence import MessagePersistenceCapability
        original, calls, triggered = MessagePersistenceCapability._save, [], []
        async def reject_checkpoint(progress, ctx, *, hook="unknown"):
            if any(isinstance(message, ModelRequest) and (message.metadata or {}).get("deepagent_output_budget") for message in progress._ledger):
                triggered.append(True)
                raise RuntimeError("injected output budget checkpoint write failure")
            return await original(progress, ctx, hook=hook)
        def response(messages, info):
            calls.append(True)
            return ModelResponse(parts=[ToolCallPart("lease_result", {}, f"bad-{len(calls)}")])
        runner = AgentRunner(Agent(FunctionModel(response), deps_type=AgentDeps), self.store, UsageLimits(request_limit=5))
        runner._output_config_loader = AsyncMock(return_value=CONTRACT)
        with patch.object(MessagePersistenceCapability, "_save", reject_checkpoint):
            with self.assertRaises(Exception):
                await runner.run(self.sid, "synthetic", AgentDeps(session_id=self.sid, agent_id=self.agent_id, user="owner"))
        self.assertTrue(triggered)
        self.assertEqual(len(calls), 1)
        rows = await self.store.list_all_events(self.sid)
        self.assertFalse([row for row in rows if row.event_type == "output/attempt" and row.payload.get("accepted")])
        self.assertEqual(len([row for row in rows if row.event_type == "execution/error"]), 1)

    async def test_post_accept_compaction_metrics_or_optional_flush_failure_keeps_completed_result(self):
        stages = [(stage, streaming) for stage in ("compaction", "metrics", "context", "title", "log") for streaming in (False, True)]
        stages.extend([("flush", True), ("loaded_skills", True)])
        for stage, streaming in stages:
            with self.subTest(stage=stage, streaming=streaming):
                sid = uuid4().hex
                def response(messages, info):
                    return ModelResponse(parts=[ToolCallPart("lease_result", {"ok": True}, "out-1")])
                async def stream(messages, info):
                    yield {0: DeltaToolCall(name="lease_result", json_args='{"ok":true}', tool_call_id="out-1")}
                runner = AgentRunner(Agent(FunctionModel(response, stream_function=stream), deps_type=AgentDeps), self.store, UsageLimits(request_limit=5))
                runner._output_config_loader = AsyncMock(return_value=CONTRACT)
                deps = AgentDeps(session_id=sid, agent_id=self.agent_id, user="owner")
                injected = RuntimeError(f"injected optional post-accept {stage} failure")
                if stage == "compaction":
                    failure = patch.object(runner, "_maybe_save_compaction", new=AsyncMock(side_effect=injected))
                elif stage == "metrics":
                    failure = patch("deep_agent.agent.runner.run_metrics", side_effect=injected)
                elif stage == "title":
                    async def bad_title():
                        raise injected
                    failure = patch.object(runner, "_start_title_generation", new=AsyncMock(return_value=asyncio.create_task(bad_title())))
                elif stage == "flush":
                    failure = patch.object(self.persister, "flush", new=AsyncMock(side_effect=injected))
                else:
                    attribute = "_context_status" if stage == "context" else "_record_loaded_skills" if stage == "loaded_skills" else "log_event"
                    if stage == "log":
                        from deep_agent.agent import runner as runner_module
                        target = runner_module
                    else:
                        target = runner
                    original = getattr(target, attribute)
                    def fail_after_accepted(*args, **kwargs):
                        if deps.output_state is not None and deps.output_state.accepted is not None:
                            raise injected
                        return original(*args, **kwargs)
                    failure = patch.object(target, attribute, side_effect=fail_after_accepted)
                with failure:
                    if streaming:
                        frames = [item async for item in runner.run_stream(sid, "synthetic", deps, persister=self.persister)]
                        self.assertEqual(frames[-1]["type"], "final")
                        self.assertEqual(frames[-1]["status"], "completed")
                        self.assertNotIn("error", [item["type"] for item in frames])
                    else:
                        self.assertEqual((await runner.run(sid, "synthetic", deps))["status"], "completed")
                rows = await self.store.list_all_events(sid)
                accepted = [row for row in rows if row.event_type == "output/attempt" and row.payload.get("accepted")]
                self.assertEqual(len(accepted), 1)
                self.assertEqual([(item.item_id, item.output) for item in project_session_events(rows).items
                    if item.kind == "structured_output"], [(accepted[0].event_id, {"ok": True})])
                self.assertFalse([row for row in rows if row.event_type == "execution/error"])
                self.assertFalse([row for row in rows if row.event_type == "assistant/message" and row.status != "completed"])

    async def test_failed_then_accepted_output_has_one_question_no_recent_error_and_real_usage_only(self):
        count, logs = 0, []
        def response(messages, info):
            nonlocal count
            count += 1
            value = {"ok": "SYNTHETIC_PRIVATE_VALUE"} if count == 1 else {"ok": True}
            return ModelResponse(parts=[ToolCallPart("lease_result", value, f"out-{count}")])
        runner = AgentRunner(Agent(FunctionModel(response), deps_type=AgentDeps), self.store, UsageLimits(request_limit=5))
        runner._output_config_loader = AsyncMock(return_value=CONTRACT)
        with patch("deep_agent.diagnostics.emit", side_effect=lambda name, **fields: logs.append((name, fields))):
            result = await runner.run(self.sid, "synthetic", AgentDeps(session_id=self.sid, agent_id=self.agent_id, user="owner"))
        self.assertEqual(result["status"], "completed")
        self.assertEqual(count, 2)
        now = datetime.now(timezone.utc)
        filters = SessionFilters(start=now - timedelta(minutes=1), end=now + timedelta(minutes=1))
        report = await DiagnosticSessionQueries(self.factory).overview(filters)
        self.assertEqual(report["metrics"]["question_count"], 1)
        self.assertEqual(report["metrics"]["error_session_count"], 0)
        self.assertEqual(report["recent_errors"], [])
        usage = await UsageQueries(self.factory).report(filters)
        self.assertEqual(usage["summary"]["requests"], 2)
        self.assertEqual(result["run_metrics"]["tool_calls"], 0)
        self.assertNotIn("SYNTHETIC_PRIVATE_VALUE", str(logs))

    async def test_accepted_output_survives_two_compactions_physical_backup_and_fresh_process(self):
        from deep_agent.agent.context.handoff import extract_protected_context, render_handoff_anchor
        with tempfile.TemporaryDirectory(prefix="TMP_to_delete_output_backup_restart_") as directory:
            path = Path(directory) / "source.sqlite"
            stores, engine, _ = await make_sqlite_stores(f"sqlite+aiosqlite:///{path}")
            old_store, old_agent = self.store, self.agent_id
            self.store, self.agent_id = stores.coordinator, await seed_default_agent(stores)
            try:
                await self.prepare()
                accepted = await self.append()
                for index in range(2):
                    if index:
                        await self.store.append(self.sid, [ModelRequest(parts=[UserPromptPart("synthetic continuation")]), ModelResponse(parts=[TextPart("synthetic old text")])],
                            agent_id=self.agent_id, user_id="owner", turn_id=uuid4().hex, run_id=uuid4().hex, terminal_status="completed")
                    raw = await self.store.get_raw_history(self.sid, agent_id=self.agent_id)
                    summary = f"Synthetic accepted output retained {index}"
                    protected = extract_protected_context(raw, cutoff=len(raw), raw_history=raw)
                    anchor = render_handoff_anchor(summary, protected_context=protected)
                    compaction = await self.store.persist_compaction_projection(self.sid, agent_id=self.agent_id,
                        user_id="owner", raw_history=raw, projected_history=[anchor], summary_markdown=summary,
                        trigger="pressure", model_id=None, estimated_before=1000, estimated_after=100,
                        budget_limit_tokens=200, context_window_tokens=4000, preserved_tail_tokens=0, duration_ms=1)
                    self.assertIsNotNone(compaction)
                    history = project_session_events(await self.store.list_all_events(self.sid))
                    self.assertEqual([(item.item_id, item.output) for item in history.items if item.kind == "structured_output"], [(accepted.event_id, {"ok": True})])
                backup = Path(directory) / "backup.sqlite"
                with sqlite3.connect(path) as source, sqlite3.connect(backup) as target:
                    source.backup(target)
                script = '''import asyncio,json,sys
from sqlalchemy.ext.asyncio import create_async_engine,async_sessionmaker
from deep_agent.db.stores.bundle import create_data_stores
from deep_agent.db.dialect import resolve_dialect
from deep_agent.conversation.projector import project_session_events
async def main():
 url="sqlite+aiosqlite:///"+sys.argv[1]; engine=create_async_engine(url)
 try:
  stores=create_data_stores(resolve_dialect(url),async_sessionmaker(engine,expire_on_commit=False))
  rows=await stores.coordinator.list_all_events(sys.argv[2]); items=project_session_events(rows).items
  print(json.dumps({"outputs":[[i.item_id,i.output] for i in items if i.kind=="structured_output"],"compactions":sum(r.event_type=="compaction" for r in rows),"snapshots":sum(bool((r.payload.get("message",{}).get("metadata") or {}).get("deepagent_output_contract")) for r in rows)},ensure_ascii=False))
 finally: await engine.dispose()
asyncio.run(main())'''
                repo = Path(__file__).resolve().parents[2]
                process = await asyncio.create_subprocess_exec(sys.executable, "-c", script, str(backup), self.sid,
                    env={**os.environ, "PYTHONPATH": str(repo / "src")}, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
                try:
                    stdout, stderr = await asyncio.wait_for(process.communicate(), 10)
                    self.assertEqual(process.returncode, 0, stderr.decode()[-1000:])
                    restored = json.loads(stdout)
                    self.assertEqual(restored, {"outputs": [[accepted.event_id, {"ok": True}]], "compactions": 2, "snapshots": 1})
                finally:
                    if process.returncode is None:
                        process.kill()
                        await process.communicate()
            finally:
                self.store, self.agent_id = old_store, old_agent
                await engine.dispose()


class StructuredOutputLiveTests(unittest.IsolatedAsyncioTestCase):
    from tests.integration import test_session_live_api as _fixture
    asyncSetUp = _fixture.SessionLiveApiTests.asyncSetUp
    asyncTearDown = _fixture.SessionLiveApiTests.asyncTearDown
    _request = _fixture.SessionLiveApiTests._request

    async def _exercise_observation(self, *, disconnect_observer=False):
        ready, release = asyncio.Event(), asyncio.Event()
        model_calls = 0
        async def stream(messages, info):
            nonlocal model_calls
            model_calls += 1
            yield {0: DeltaToolCall(name="lease_result", json_args='{"ok":', tool_call_id="once")}
            ready.set()
            await release.wait()
            yield {0: DeltaToolCall(json_args='true}')}
        runner = AgentRunner(Agent(FunctionModel(stream_function=stream), deps_type=AgentDeps), self.ctx.store,
                             UsageLimits(request_limit=3), active_runs=self.ctx.active_runs)
        runner._output_config_loader = AsyncMock(return_value=CONTRACT)
        with patch("deep_agent.api.v1.chat.build_runner_for_agent", new=AsyncMock(return_value=runner)), \
             patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock):
            producer = self._request("/api/v1/chat/stream", method="POST", body={
                "agent_id": self.agent_id, "session_id": self.session, "message": "synthetic structured output"})
            self.assertEqual((await producer.started())["status"], 200)
            run_id = (await producer.frame())[1]["run_id"]
            await asyncio.wait_for(ready.wait(), 5)
            self.assertFalse([row for row in await self.ctx.store.list_all_events(self.session) if row.event_type == "output/attempt"])
            observer = self._request(query={"agent_id": self.agent_id, "run_id": run_id})
            self.assertEqual((await observer.started())["status"], 200)
            sync = await observer.frame()
            self.assertEqual(sync[0], "observation_sync")
            baseline = sync[1]["baseline_sequence"]
            if disconnect_observer:
                await observer.close()
            release.set()
            produced = await producer.until("final")
            final = produced[-1][1]
            outputs = [data for name, data in produced if name == "output"]
            self.assertEqual(len(outputs), 1)
            self.assertGreater(outputs[0]["sequence"], baseline)
            self.assertEqual(final["output_info"]["output_id"], outputs[0]["output_id"])
            if not disconnect_observer:
                observed = await observer.until("observation_end")
                observed_output = [frame[1]["data"] for frame in observed if frame[0] == "observation" and frame[1]["event"] == "output"]
                self.assertEqual(observed_output, outputs)
            history = (await self.http.get(f"/api/v1/sessions/{self.session}/history", params={"agent_id": self.agent_id})).json()
            accepted = [item for item in history["items"] if item["kind"] == "structured_output"]
            self.assertEqual(len(accepted), 1)
            self.assertEqual((accepted[0]["item_id"], accepted[0]["output"]), (outputs[0]["output_id"], final["output"]))
            self.assertEqual(model_calls, 1)
            after, paged = 0, []
            while True:
                response = await self.http.get(f"/api/v1/sessions/{self.session}/events", params={"agent_id": self.agent_id, "limit": 2, "after_sequence": after})
                self.assertEqual(response.status_code, 200, response.text)
                page = response.json()
                paged.extend(page["items"])
                if page["next_after_sequence"] is None:
                    break
                after = page["next_after_sequence"]
            self.assertEqual(len({item["event_id"] for item in paged}), len(paged))
            self.assertEqual([item["sequence"] for item in paged], sorted(item["sequence"] for item in paged))
            semantic = next(item for item in paged if item["event_id"] == accepted[0]["item_id"])
            self.assertTrue(set(semantic["payload"]["source"]["event_ids"]) <= {item["event_id"] for item in paged})
            self.assertEqual(self.ctx.active_runs.active_count(), 0)
            await asyncio.wait_for(producer.task, 5)

    async def test_post_live_history_share_identity_and_native_value(self):
        await self._exercise_observation()

    async def test_observer_disconnect_does_not_cancel_accepted_output(self):
        await self._exercise_observation(disconnect_observer=True)


class AgentOutputListScaleTests(unittest.IsolatedAsyncioTestCase):
    from tests.integration import test_agent_packages_api as _fixture
    asyncSetUp = _fixture.AgentPackagesApiTests.asyncSetUp
    asyncTearDown = _fixture.AgentPackagesApiTests.asyncTearDown

    async def test_ten_hundred_thousand_agents_use_one_list_query_and_one_batch_resolver(self):
        for count in (10, 100, 1000):
            with self.subTest(count=count):
                async with self.engine.begin() as db:
                    await db.execute(delete(AgentRow))
                    await db.execute(insert(AgentRow), [{"id": str(uuid4()), "name": f"scale-{index}",
                        "prompt": "synthetic", "updated_by": f"operator-{index % 25}"} for index in range(count)])
                statements, actor_ids = [], []
                def capture(conn, cursor, statement, parameters, context, executemany):
                    if statement.lstrip().upper().startswith("SELECT"):
                        statements.append(statement)
                async def profiles(request, current_user, ids):
                    actor_ids.extend(ids)
                    return {actor: {"id": actor, "username": actor, "resolution": "resolved"} for actor in set(actor_ids)}
                resolver = AsyncMock(side_effect=profiles)
                event.listen(self.engine.sync_engine, "before_cursor_execute", capture)
                started = time.perf_counter()
                try:
                    with patch("deep_agent.api.v1.agents.resolve_diagnostic_users", resolver):
                        response = await self.client.get("/api/v1/agents", headers=self.headers)
                finally:
                    event.remove(self.engine.sync_engine, "before_cursor_execute", capture)
                self.assertEqual(response.status_code, 200, response.text[:300])
                self.assertEqual(len(response.json()), count)
                self.assertEqual(resolver.await_count, 1)
                self.assertEqual(len(set(actor_ids)), min(count, 25))
                self.assertEqual(len(statements), 1, statements)
                self.assertFalse(any("agent_output_configs" in sql for sql in statements))
                self.assertTrue(all("output_config" not in row and row["updated_by_user"]["username"].startswith("operator-") for row in response.json()))
                self.assertLess(time.perf_counter() - started, 5, "bounded list fixture unexpectedly stalled")


if __name__ == "__main__":
    unittest.main()
