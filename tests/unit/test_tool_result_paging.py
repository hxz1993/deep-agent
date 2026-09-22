from __future__ import annotations

import asyncio
import unittest
from unittest.mock import AsyncMock

from pydantic_ai import Agent, ModelRetry, RunContext
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.context.estimator import TokenEstimator
from deep_agent.agent.context.file_keys import deep_agent_file_key
from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.tool_output_limits import build_tool_output_limits


class ToolResultPagingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.capability, self.store = build_tool_output_limits(
            request_headers={}, context_window_tokens=1_000_000,
            max_output_tokens=8_000, estimator=TokenEstimator.conservative(),
        )
        self.deps = AgentDeps(session_id="test-paging")
        self.store.bind(self.deps, [])
        self.ctx = RunContext(deps=self.deps, model=TestModel(), usage=RunUsage())

    async def read(self, **args):
        toolset = self.capability.get_toolset()
        tools = await toolset.get_tools(self.ctx)
        return await toolset.call_tool("read_tool_result", {"handle": "fixture", **args}, self.ctx, tools["read_tool_result"])

    async def resume(self, args):
        args = dict(args)
        args.pop("handle")
        return await self.read(**args)

    async def test_default_and_maximum_are_doubled(self) -> None:
        self.store.read = AsyncMock(return_value="\n".join(map(str, range(3000))).encode())
        page = await self.read()
        self.assertEqual(page["returned_count"], 400)
        self.assertEqual(page["next_read"]["offset"], 400)
        page = await self.read(limit=100_000)
        self.assertEqual(page["returned_count"], 2000)
        self.assertEqual(page["limit"], 2000)

    async def test_long_unicode_line_round_trips_without_missing_suffix(self) -> None:
        original = "文😀" * 60_005
        self.store.read = AsyncMock(return_value=original.encode())
        chunks = []
        page = await self.read()
        for _ in range(5):
            self.assertLessEqual(len(page["content"]), 50_000)
            chunks.append(page["content"])
            if page["next_read"] is None:
                break
            self.assertEqual(page["next_read"]["offset"], 0)
            page = await self.resume(page["next_read"])
        self.assertIsNone(page["next_read"])
        self.assertEqual("".join(chunks), original)
        self.assertFalse(page["has_more"])

    async def test_complete_jsonl_records_are_not_split_to_fill_previous_page(self) -> None:
        first, second = '{"text":"' + "甲" * 30_000 + '"}', '{"text":"' + "乙" * 30_000 + '"}'
        self.store.read = AsyncMock(return_value=(first + "\n" + second).encode())
        page = await self.read()
        self.assertEqual(page["content"], first)
        self.assertEqual(page["next_read"]["offset"], 1)
        self.assertEqual(page["next_read"]["char_offset"], 0)
        tail = await self.resume(page["next_read"])
        self.assertEqual(tail["content"], second)
        self.assertIsNone(tail["next_read"])

    async def test_tail_window_resumes_then_exposes_older_window(self) -> None:
        line = "中" * 60_000
        self.store.read = AsyncMock(return_value=("older\n" + line + "\nlast\nskipped").encode())
        page = await self.read(from_end=True, offset=1, limit=2)
        previous = page["previous_read"]
        self.assertEqual(page["offset"], 1)
        self.assertEqual(page["content"], line[:50_000])
        tail = await self.resume(page["next_read"])
        self.assertEqual(tail["content"], line[50_000:] + "\nlast")
        self.assertIsNone(tail["next_read"])
        older = await self.resume(previous)
        self.assertEqual(older["content"], "older")

    async def test_literal_filter_and_empty_pages(self) -> None:
        self.store.read = AsyncMock(return_value=b"abc\n[.*] one\nxyz\n[.*] two")
        page = await self.read(pattern="[.*]", limit=1)
        self.assertEqual(page["content"], "[.*] one")
        self.assertEqual(page["source_line_numbers"], [2])
        self.assertEqual(page["source_total_count"], 4)
        tail = await self.resume(page["next_read"])
        self.assertEqual(tail["content"], "[.*] two")
        self.assertEqual(tail["source_line_numbers"], [4])
        self.assertIsNone(tail["next_read"])
        empty = await self.read(pattern="absent")
        self.assertEqual(empty["content"], "")
        self.assertEqual(empty["source_line_numbers"], [])
        self.assertFalse(empty["has_more"])
        empty = await self.read(offset=100)
        self.assertEqual(empty["content"], "")

    async def test_context_merges_and_paginates_selected_lines_with_source_positions(self) -> None:
        lines = ["start", "[hit] one", "middle", "[hit] two", "after", "gap", "gap", "before", "[hit] three", "end"]
        self.store.read = AsyncMock(return_value="\n".join(lines).encode())
        page = await self.read(pattern="[hit]", context_lines=1, limit=3)
        contents, positions = [], []
        for _ in range(4):
            self.assertEqual(page["total_count"], 8)
            self.assertEqual(page["source_total_count"], 10)
            contents.extend(page["content"].splitlines())
            positions.extend(page["source_line_numbers"])
            if page["next_read"] is None:
                break
            self.assertEqual(page["next_read"]["context_lines"], 1)
            page = await self.resume(page["next_read"])
        self.assertIsNone(page["next_read"])
        self.assertEqual(positions, [1, 2, 3, 4, 5, 8, 9, 10])
        self.assertEqual(contents, [lines[index - 1] for index in positions])

    async def test_context_clips_at_boundaries_and_no_pattern_keeps_all_lines(self) -> None:
        self.store.read = AsyncMock(return_value=b"hit\n\nlast hit")
        page = await self.read(pattern="hit", context_lines=5)
        self.assertEqual(page["content"], "hit\n\nlast hit")
        self.assertEqual(page["source_line_numbers"], [1, 2, 3])
        plain = await self.read(context_lines=5)
        self.assertEqual(plain, page)
        empty = await self.read(pattern="absent", context_lines=3)
        self.assertEqual(empty["content"], "")
        self.assertEqual(empty["source_line_numbers"], [])
        self.assertEqual(empty["total_count"], 0)
        self.assertEqual(empty["source_total_count"], 3)

    async def test_context_tail_cursor_and_long_line_keep_original_positions(self) -> None:
        long_line = "😀" * 60_000
        self.store.read = AsyncMock(return_value=("older hit\nold context\ngap\n" + long_line + "\nnew hit\nlast").encode())
        page = await self.read(pattern="hit", context_lines=1, from_end=True, limit=3)
        previous = page["previous_read"]
        self.assertEqual(page["source_line_numbers"], [4])
        self.assertEqual(page["content"], long_line[:50_000])
        self.assertEqual(previous["context_lines"], 1)
        self.assertEqual(page["next_read"]["context_lines"], 1)
        tail = await self.resume(page["next_read"])
        self.assertEqual(tail["source_line_numbers"], [4, 5, 6])
        self.assertEqual(tail["content"], long_line[50_000:] + "\nnew hit\nlast")
        self.assertIsNone(tail["next_read"])
        older = await self.resume(previous)
        self.assertEqual(older["source_line_numbers"], [1, 2])
        self.assertEqual(older["content"], "older hit\nold context")

    async def test_invalid_context_is_rejected_before_store_read(self) -> None:
        self.store.read = AsyncMock(return_value=b"hit")
        for context in [-1, 6, 1.5, True, "3"]:
            with self.subTest(context=context), self.assertRaises(ModelRetry):
                await self.read(context_lines=context)
        self.store.read.assert_not_awaited()

    async def test_invalid_cursors_do_not_silently_skip(self) -> None:
        self.store.read = AsyncMock(return_value=b"abc")
        for args in [{"offset": -1}, {"limit": 0}, {"char_offset": -1}, {"char_offset": 3}, {"char_offset": 4}, {"offset": 5, "char_offset": 1}, {"offset": 2, "end_offset": 1}, {"from_end": True, "end_offset": 1}]:
            with self.subTest(args=args), self.assertRaises(ModelRetry):
                await self.read(**args)

    async def test_authorization_failure_is_not_a_retry_or_data_leak(self) -> None:
        page = await self.read()
        self.assertIn("error", page)
        self.assertNotIn("content", page)
        self.assertNotIn("fixture", page["error"])

    async def test_cancellation_propagates(self) -> None:
        self.store.read = AsyncMock(side_effect=asyncio.CancelledError)
        with self.assertRaises(asyncio.CancelledError):
            await self.read()

    async def test_real_agent_receives_new_schema_and_structured_page(self) -> None:
        self.store.read = AsyncMock(return_value=b"first\nlast")

        def model(messages, info):
            definition = next(t for t in info.function_tools if t.name == "read_tool_result")
            self.assertEqual(definition.parameters_json_schema["properties"]["limit"]["default"], 400)
            self.assertIn("char_offset", definition.parameters_json_schema["properties"])
            context_schema = definition.parameters_json_schema["properties"]["context_lines"]
            self.assertEqual(context_schema["default"], 0)
            self.assertEqual(context_schema["minimum"], 0)
            self.assertEqual(context_schema["maximum"], 5)
            if len(messages) == 1:
                return ModelResponse(parts=[ToolCallPart("read_tool_result", {"handle": "fixture"}, "read-1")])
            returned = [p for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, ToolReturnPart)]
            self.assertEqual(returned[-1].content["content"], "first\nlast")
            self.assertEqual(returned[-1].content["source_line_numbers"], [1, 2])
            return ModelResponse(parts=[TextPart("done")])

        agent = Agent(FunctionModel(model), deps_type=AgentDeps, capabilities=[self.capability])
        result = await agent.run("read fixture", deps=self.deps)
        self.assertEqual(result.output, "done")

    def test_compaction_keys_distinguish_character_slices_and_boundaries(self) -> None:
        for name, base in [("read_tool_result", {"handle": "fixture"}), ("read_file", {"resource": {"type": "attachment", "file_id": "fixture"}})]:
            first = deep_agent_file_key(ToolCallPart(name, base))
            resumed = deep_agent_file_key(ToolCallPart(name, {**base, "char_offset": 50_000}))
            self.assertNotEqual(first, resumed)
        self.assertNotEqual(
            deep_agent_file_key(ToolCallPart("read_tool_result", {"handle": "fixture", "end_offset": 3})),
            deep_agent_file_key(ToolCallPart("read_tool_result", {"handle": "fixture", "end_offset": 4})),
        )
        self.assertNotEqual(
            deep_agent_file_key(ToolCallPart("read_tool_result", {"handle": "fixture", "pattern": "hit"})),
            deep_agent_file_key(ToolCallPart("read_tool_result", {"handle": "fixture", "pattern": "hit", "context_lines": 3})),
        )
