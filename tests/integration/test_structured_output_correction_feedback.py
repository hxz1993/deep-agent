from __future__ import annotations

import copy
import json
import unittest
from unittest.mock import AsyncMock, patch
from uuid import uuid4

from pydantic_ai import Agent
from pydantic_ai.messages import ModelResponse, ThinkingPart, ToolCallPart, UserPromptPart
from pydantic_ai.models.function import DeltaThinkingPart, DeltaToolCall, FunctionModel
from pydantic_ai.usage import UsageLimits

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.event_persister import EventPersister
from deep_agent.agent.runner import AgentRunner
from deep_agent.conversation.projector import project_session_events
from tests.helpers.sqlite_stores import make_sqlite_stores, seed_default_agent


_OUTPUT_NAME = "recon_feedback"
_PRIVATE_VALUE = "PRIVATE-CANDIDATE-VALUE-MUST-NOT-APPEAR-IN-FEEDBACK"
_SCHEMA = {
    "type": "object",
    "properties": {
        "recon_mapping": {"type": "null"},
        "recon_analysis": {
            "anyOf": [
                {
                    "type": "object",
                    "properties": {
                        "hypotheses": {"type": "array", "items": {"type": "string"}},
                        "questions": {"type": "array", "items": {"type": "string"}, "maxItems": 4},
                        "nextSteps": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["hypotheses", "questions", "nextSteps"],
                    "additionalProperties": False,
                },
                {"type": "null"},
            ]
        },
    },
    "required": ["recon_mapping", "recon_analysis"],
    "additionalProperties": False,
}
_VALID = {
    "recon_mapping": None,
    "recon_analysis": {
        "hypotheses": [],
        "questions": ["Check the original evidence."],
        "nextSteps": ["Ask the owner to verify."],
    },
}


class StructuredOutputCorrectionFeedbackTests(unittest.IsolatedAsyncioTestCase):
    """Synthetic failures cross the real SDK, validator process and Event Store."""

    async def asyncSetUp(self):
        self.stores, self.engine, _ = await make_sqlite_stores()
        self.agent_id = await seed_default_agent(self.stores)
        self.persister = EventPersister(self.stores.coordinator)
        await self.persister.start()
        self.title = patch.object(AgentRunner, "_generate_model_title", new_callable=AsyncMock)
        self.title.start()

    async def asyncTearDown(self):
        self.title.stop()
        await self.persister.aclose()
        await self.engine.dispose()

    async def exercise(self, bad_parts, *, streaming, schema=None, valid=None, persistent=False):
        schema = copy.deepcopy(_SCHEMA if schema is None else schema)
        valid = copy.deepcopy(_VALID if valid is None else valid)
        requests, retries, executed = [], [], []

        def respond(messages, info):
            requests.append(info)
            prompts = [
                p.content.split("<pyai:retry_feedback>", 1)[1].split("</pyai:retry_feedback>", 1)[0].strip()
                for m in messages for p in m.parts
                if isinstance(p, UserPromptPart) and isinstance(p.content, str)
                and "<pyai:retry_feedback>" in p.content
            ]
            retries.append(prompts[-1] if prompts else None)
            parts = (copy.deepcopy(bad_parts) if len(requests) == 1 or persistent else
                     [ToolCallPart("output__" + _OUTPUT_NAME, copy.deepcopy(valid), "good-result")])
            return ModelResponse(parts=parts, finish_reason="stop" if isinstance(parts[0], ThinkingPart) else "tool_call")

        async def stream(messages, info):
            response = respond(messages, info)
            for index, part in enumerate(response.parts):
                if isinstance(part, ThinkingPart):
                    yield {index: DeltaThinkingPart(content=part.content)}
                else:
                    raw = part.args if isinstance(part.args, str) else json.dumps(part.args)
                    # Exercise genuine incremental argument assembly, including malformed JSON.
                    midpoint = max(1, len(raw) // 2)
                    yield {index: DeltaToolCall(name=part.tool_name, json_args=raw[:midpoint], tool_call_id=part.tool_call_id)}
                    yield {index: DeltaToolCall(json_args=raw[midpoint:])}

        agent = Agent(FunctionModel(respond, stream_function=stream), deps_type=AgentDeps,
                      retries=2, end_strategy="graceful")

        @agent.tool_plain
        async def lookup():
            executed.append(True)
            return "not permitted during rejected response"

        runner = AgentRunner(agent, self.stores.coordinator, UsageLimits(request_limit=10))
        runner._output_config_loader = AsyncMock(return_value={
            "name": _OUTPUT_NAME, "description": "Return the complete synthetic reconciliation result.",
            "json_schema": schema,
        })
        sid = uuid4().hex
        deps = AgentDeps(session_id=sid, user="feedback-test-user", agent_id=self.agent_id)
        frames = []
        if persistent:
            if streaming:
                frames = [event async for event in runner.run_stream(sid, "synthetic fixture", deps,
                                                                   persister=self.persister)]
                self.assertEqual(frames[-1]["type"], "error")
                self.assertIn("output_retry_exhausted", frames[-1]["message"])
            else:
                with self.assertRaisesRegex(Exception, "output_retry_exhausted"):
                    await runner.run(sid, "synthetic fixture", deps)
            result = None
        elif streaming:
            frames = [event async for event in runner.run_stream(sid, "synthetic fixture", deps,
                                                               persister=self.persister)]
            self.assertEqual(frames[-1]["type"], "final", frames[-1])
            result = frames[-1]
        else:
            result = await runner.run(sid, "synthetic fixture", deps)

        rows = await self.stores.coordinator.list_all_events(sid)
        attempts = [row for row in rows if row.event_type == "output/attempt"]
        history = project_session_events(rows)
        accepted = [item for item in history.items if item.kind == "structured_output"]
        self.assertFalse(executed, "Rejected mixed output must never execute its ordinary tool")
        self.assertFalse([item for item in history.items if item.kind == "tool"])
        self.assertEqual(len(requests), 3 if persistent else 2)
        self.assertEqual([row.payload["attempt"] for row in attempts], [1, 2, 3] if persistent else [1, 2])
        self.assertFalse(attempts[0].payload["accepted"])
        self.assertNotIn("instruction", attempts[0].payload, "Retry instructions are not a new persisted protocol")
        self.assertEqual(requests[0].output_tools, requests[-1].output_tools)
        self.assertTrue(all({"path", "constraint", "message"}.issubset(error)
                            for row in attempts if not row.payload["accepted"] for error in row.payload["errors"]))
        if persistent:
            self.assertFalse(accepted)
            self.assertFalse(any(row.payload["accepted"] for row in attempts))
            self.assertEqual(sum(row.event_type == "execution/error" for row in rows), 1)
        else:
            self.assertTrue(attempts[-1].payload["accepted"])
            self.assertEqual(result["output"], valid)
            self.assertEqual(len(accepted), 1)
            self.assertEqual(accepted[0].output, valid)
            self.assertEqual(accepted[0].item_id, result["output_info"]["output_id"])
            self.assertEqual(accepted[0].item_id, attempts[-1].event_id)
            self.assertEqual(result["output_info"]["attempt"], 2)
            self.assertEqual(result["output_info"]["tool_call_id"], "good-result")
            self.assertEqual(sum(row.event_type == "execution/error" for row in rows), 0)
        if streaming:
            outputs = [frame for frame in frames if frame.get("type") == "output"]
            self.assertEqual(len(outputs), len(attempts))
            self.assertEqual([frame["accepted"] for frame in outputs],
                             [False, False, False] if persistent else [False, True])
            if not persistent:
                self.assertEqual(outputs[-1]["output_id"], result["output_info"]["output_id"])
        feedback = json.loads(retries[1])
        self.assertIsInstance(feedback.get("instruction"), str)
        self.assertIn(_OUTPUT_NAME, feedback["instruction"])
        self.assertNotIn(_PRIVATE_VALUE, retries[1])
        self.assertLessEqual(len(retries[1]), 6000)
        return feedback, retries, attempts

    async def test_real_record_failure_shapes_have_actionable_feedback_and_recover(self):
        duplicate = ('{"recon_mapping":null,"recon_analysis":{"hypotheses":[],"questions":["' +
                     _PRIVATE_VALUE + '"],"nextSteps":[],"hypotheses":[]}}')
        incomplete = json.dumps(_VALID)[:-1]
        excess = copy.deepcopy(_VALID)
        excess["recon_analysis"]["questions"] = [_PRIVATE_VALUE] * 5
        cases = (
            ("missing_output", [ThinkingPart(_PRIVATE_VALUE)], "missing_output"),
            ("duplicate", [ToolCallPart("output__" + _OUTPUT_NAME, duplicate, "bad-result")], "invalid_json"),
            ("syntax", [ToolCallPart("output__" + _OUTPUT_NAME, incomplete, "bad-result")], "invalid_json"),
            ("nullable_max_items", [ToolCallPart("output__" + _OUTPUT_NAME, excess, "bad-result")], "schema_validation_failed"),
        )
        for streaming in (False, True):
            for label, parts, code in cases:
                with self.subTest(streaming=streaming, failure=label):
                    feedback, _, _ = await self.exercise(parts, streaming=streaming)
                    self.assertEqual(feedback["code"], code)
                    if code != "schema_validation_failed":
                        self.assertTrue(all(error.get("code") == code for error in feedback["errors"]))
                    message = " ".join(error["message"] for error in feedback["errors"]).lower()
                    if label == "missing_output":
                        self.assertIn("complete", feedback["instruction"].lower())
                    elif label == "duplicate":
                        self.assertTrue(any(error["constraint"] == "duplicateKey" for error in feedback["errors"]))
                        self.assertIn("hypotheses", message)
                    elif label == "syntax":
                        self.assertTrue(any(error["constraint"] == "json" for error in feedback["errors"]))
                        self.assertIn("line", message)
                        self.assertIn("column", message)
                    else:
                        errors = [error for error in feedback["errors"] if error["constraint"] == "maxItems"]
                        self.assertTrue(errors, feedback)
                        self.assertEqual(errors[0]["path"], "/recon_analysis/questions")
                        self.assertIn("4", errors[0]["message"])
                        self.assertIn("5", errors[0]["message"])

    async def test_protocol_guards_give_actions_without_dispatching_tools(self):
        cases = (
            ("mixed_output_calls", [ToolCallPart("lookup", {}, "lookup-rejected"),
                                    ToolCallPart("output__" + _OUTPUT_NAME, _VALID, "bad-result")]),
            ("multiple_output_calls", [ToolCallPart("output__" + _OUTPUT_NAME, _VALID, "bad-first"),
                                       ToolCallPart("output__" + _OUTPUT_NAME, _VALID, "bad-second")]),
        )
        for streaming in (False, True):
            for code, parts in cases:
                with self.subTest(streaming=streaming, code=code):
                    feedback, _, _ = await self.exercise(parts, streaming=streaming)
                    self.assertEqual(feedback["code"], code)
                    self.assertTrue(feedback["errors"])
                    self.assertTrue(all(error.get("code") == code for error in feedback["errors"]))
                    instruction = feedback["instruction"].lower()
                    self.assertTrue(any(word in instruction for word in ("only", "separate", "single", "one")), instruction)

    async def test_two_corrections_are_shared_and_third_rejection_is_terminal(self):
        for streaming in (False, True):
            with self.subTest(streaming=streaming):
                feedback, retries, attempts = await self.exercise(
                    [ToolCallPart("output__" + _OUTPUT_NAME, "{", "bad-result")], streaming=streaming, persistent=True)
                self.assertEqual(feedback["code"], "invalid_json")
                self.assertEqual(len([retry for retry in retries if retry is not None]), 2)
                self.assertEqual(len(attempts), 3)

    async def test_many_detailed_errors_keep_retry_json_complete_and_bounded(self):
        keys = [f"field_{index}_" + "x" * 140 for index in range(30)]
        schema = {"type": "object", "properties": {key: {"type": "string"} for key in keys},
                  "required": keys, "additionalProperties": False}
        feedback, retries, attempts = await self.exercise(
            [ToolCallPart("output__" + _OUTPUT_NAME, {}, "bad-result")], streaming=False,
            schema=schema, valid={key: "ok" for key in keys})
        self.assertEqual(feedback["code"], "schema_validation_failed")
        self.assertGreater(feedback.get("truncated_errors", 0), 0)
        self.assertLess(len(feedback["errors"]), len(attempts[0].payload["errors"]))
        self.assertLessEqual(len(retries[1]), 6000)


if __name__ == "__main__":
    unittest.main()
