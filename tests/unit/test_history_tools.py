from __future__ import annotations

import unittest

from pydantic_ai import RunContext
from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    ThinkingPart,
    ToolCallPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_ai.models.test import TestModel
from pydantic_ai.usage import RunUsage

from deep_agent.agent.deps import AgentDeps
from deep_agent.agent.history_tools import build_history_toolset, score_search_text
from deep_agent.schemas.agent import AgentCreate
from tests.helpers.sqlite_stores import make_sqlite_stores


class ConversationHistoryToolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.stores, self.engine, _ = await make_sqlite_stores()
        model = (await self.stores.llm_model.list_all())[0]
        self.agent = await self.stores.agent.create_unique(
            AgentCreate(name="history-tools", model_id=model.id, prompt="test")
        )
        self.store = self.stores.coordinator
        await self.store.append(
            "session-cn",
            [
                ModelRequest(
                    parts=[
                        UserPromptPart(
                            content=[
                                "查询华东区域成员",
                                {
                                    "kind": "attachment-ref",
                                    "id": "local_budget_1",
                                    "name": "华东预算.xlsx",
                                    "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                                },
                            ]
                        )
                    ]
                ),
                ModelResponse(
                    parts=[
                        ThinkingPart(content="这段私有思考不应被搜索"),
                        ToolCallPart(
                            "query_members",
                            {"parent": "华东", "path": "/区域/华东"},
                            "call-cn-1",
                        ),
                        ToolCallPart(
                            "other_tool",
                            {"scope": "unrelated"},
                            "call-other-1",
                        ),
                    ]
                ),
                ModelRequest(
                    parts=[
                        ToolReturnPart(
                            "query_members",
                            {"names": ["上海", "江苏"], "total": 2},
                            "call-cn-1",
                            metadata={
                                "overflow_handle": "artifact-1",
                                "authorization": "secret",
                            },
                        ),
                        ToolReturnPart(
                            "other_tool",
                            {"ok": True},
                            "call-other-1",
                        ),
                    ]
                ),
            ],
            agent_id=self.agent.id,
            user_id="user-a",
            turn_id="1" * 32,
            run_id="run-cn",
        )
        await self.store.append(
            "session-other",
            [ModelRequest(parts=[UserPromptPart(content="另一个用户的上海数据")])],
            agent_id=self.agent.id,
            user_id="user-b",
            turn_id="2" * 32,
            run_id="run-other",
        )
        self.toolset = build_history_toolset(self.store)
        self.deps = AgentDeps(
            session_id="session-cn",
            agent_id=self.agent.id,
            user="user-a",
        )
        self.ctx = RunContext(
            deps=self.deps,
            model=TestModel(),
            usage=RunUsage(),
            run_id="history-read-run",
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _call(self, name: str, **kwargs):
        return await self.toolset.tools[name].function(self.ctx, **kwargs)

    async def test_chinese_search_ranks_substring_and_bigrams(self) -> None:
        self.assertGreater(
            score_search_text("华东成员", "查询华东区域成员并返回上海江苏"),
            score_search_text("华东成员", "查询华南预算"),
        )
        result = await self._call(
            "search_conversation_history",
            query="上海成员",
            scope="my_sessions",
        )
        self.assertTrue(result["items"])
        self.assertTrue(
            any(item["session_id"] == "session-cn" for item in result["items"])
        )
        self.assertFalse(
            any(item["session_id"] == "session-other" for item in result["items"])
        )
        self.assertFalse(
            any("私有思考" in item.get("preview", "") for item in result["items"])
        )

    async def test_call_id_reads_pair_and_redacts_metadata(self) -> None:
        result = await self._call(
            "read_conversation_event",
            call_id="call-cn-1",
        )
        self.assertEqual(
            [item["event_type"] for item in result["events"]],
            ["assistant/message", "tool/results"],
        )
        self.assertNotIn("secret", str(result))
        self.assertNotIn("metadata", str(result))

    async def test_attachment_name_search_returns_exact_part_location(self) -> None:
        result = await self._call(
            "search_conversation_history",
            query="华东预算.xlsx",
        )
        user_event = next(
            item for item in result["items"] if item["event_type"] == "user/message"
        )
        attachment = next(
            part for part in user_event["matched_parts"] if part.get("file_id")
        )
        self.assertEqual(attachment["file_id"], "local_budget_1")
        self.assertEqual(attachment["part_offset"], 0)
        self.assertEqual(attachment["item_offset"], 1)

    async def test_tool_filter_returns_only_the_matching_part(self) -> None:
        result = await self._call(
            "search_conversation_history",
            query="华东",
            tool_name="query_members",
        )
        matching = [
            item for item in result["items"]
            if item["event_type"] == "assistant/message"
        ]
        self.assertTrue(matching)
        self.assertEqual(matching[0]["tool_name"], "query_members")
        self.assertEqual(matching[0]["call_id"], "call-cn-1")
        self.assertTrue(
            all(
                part["tool_name"] == "query_members"
                for part in matching[0]["matched_parts"]
            )
        )

    async def test_exact_event_is_not_readable_by_another_user(self) -> None:
        event = (
            await self.store.list_events(
                "session-cn", event_type="user/message", limit=10
            )
        )[0]
        other_ctx = RunContext(
            deps=AgentDeps(
                session_id="session-other",
                agent_id=self.agent.id,
                user="user-b",
            ),
            model=TestModel(),
            usage=RunUsage(),
            run_id="other-read-run",
        )
        with self.assertRaisesRegex(Exception, "not found or not authorized"):
            await self.toolset.tools["read_conversation_event"].function(
                other_ctx,
                event_id=event.event_id,
            )


if __name__ == "__main__":
    unittest.main()
