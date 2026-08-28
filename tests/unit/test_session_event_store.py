from __future__ import annotations

import asyncio
import unittest

from pydantic_ai.messages import (
    ModelRequest,
    ModelResponse,
    TextPart,
    UserPromptPart,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from deep_agent.db.conversation import SqlConversationCoordinator
from deep_agent.db.dialect import DbDialect
from deep_agent.db.models import Agent, Base, LlmModel, LlmProvider
from deep_agent.db.stores.bundle import create_data_stores


class SessionEventStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, expire_on_commit=False)
        self.stores = create_data_stores(DbDialect.SQLITE, self.factory)
        self.store: SqlConversationCoordinator = self.stores.coordinator  # type: ignore[assignment]
        async with self.factory() as db:
            provider = LlmProvider(
                id="1" * 32,
                name="Provider",
                provider_type="openai_compatible",
                use_default_base_url=False,
                base_url="http://example.invalid/v1",
                api_key=None,
            )
            model = LlmModel(
                id="00000000-0000-4000-8000-000000000099",
                provider_id=provider.id,
                name="Model",
                model="test:model",
            )
            agent = Agent(
                id="00000000-0000-4000-8000-000000000001",
                name="Agent",
                model_id=model.id,
                prompt="test",
            )
            db.add_all([provider, model, agent])
            await db.commit()
        self.agent_id = "00000000-0000-4000-8000-000000000001"

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_appends_messages_and_restores_messages(self) -> None:
        messages = [
            ModelRequest(parts=[UserPromptPart(content="查询成员")], run_id="run-1"),
            ModelResponse(parts=[TextPart(content="结果")], run_id="run-1"),
        ]
        rows = await self.store.append(
            "session-1",
            messages,
            agent_id=self.agent_id,
            user_id="user-1",
            app_id="app001",
            space_id="app",
            turn_id="a" * 32,
            run_id="run-1",
        )

        self.assertEqual([row.sequence for row in rows], [1, 2])
        self.assertEqual([row.event_type for row in rows], ["user/message", "assistant/message"])
        self.assertEqual(
            await self.store.get_history("session-1", agent_id=self.agent_id),
            messages,
        )
        events = await self.store.list_events("session-1")
        self.assertEqual([event.event_id for event in events], [row.event_id for row in rows])

    async def test_lifecycle_updates_same_event(self) -> None:
        run = await self.store.append_lifecycle(
            "session-2",
            event_type="compaction",
            status="running",
            agent_id=self.agent_id,
            user_id="user-1",
            turn_id="b" * 32,
            run_id="run-2",
            payload={"model_id": "model"},
        )
        updated = await self.store.update_lifecycle(
            run.event_id,
            status="completed",
            payload={"model_id": "model", "duration_ms": 10},
        )
        duplicate = await self.store.update_lifecycle(
            run.event_id,
            status="failed",
            payload={"error": "late"},
        )

        self.assertIsNotNone(updated)
        assert updated is not None
        self.assertEqual(updated.event_id, run.event_id)
        self.assertEqual(updated.status, "completed")
        self.assertIsNone(duplicate)
        self.assertEqual(len(await self.store.list_events("session-2")), 1)

    async def test_message_append_is_idempotent_and_conflicts_fail_closed(self) -> None:
        original = [ModelRequest(parts=[UserPromptPart(content="same")], run_id="run-idem")]
        first = await self.store.append(
            "session-idem",
            original,
            agent_id=self.agent_id,
            user_id="user-1",
            turn_id="d" * 32,
            run_id="run-idem",
        )
        retry = await self.store.append(
            "session-idem",
            original,
            agent_id=self.agent_id,
            user_id="user-1",
            turn_id="d" * 32,
            run_id="run-idem",
        )
        self.assertEqual([row.event_id for row in retry], [row.event_id for row in first])
        self.assertEqual(len(await self.store.list_events("session-idem")), 1)

        conflicting = [
            ModelRequest(parts=[UserPromptPart(content="different")], run_id="run-idem")
        ]
        with self.assertRaisesRegex(ValueError, "idempotency conflict"):
            await self.store.append(
                "session-idem",
                conflicting,
                agent_id=self.agent_id,
                user_id="user-1",
                turn_id="d" * 32,
                run_id="run-idem",
            )

    async def test_exceptional_terminal_is_saved_only_without_assistant_response(self) -> None:
        terminal = await self.store.append_terminal(
            "session-terminal",
            status="failed",
            agent_id=self.agent_id,
            user_id="user-1",
            turn_id="run-failed",
            run_id="run-failed",
            payload={"error": "ProviderError"},
        )
        self.assertIsNotNone(terminal)
        assert terminal is not None
        self.assertEqual(terminal.event_type, "run/terminal")
        self.assertEqual(terminal.status, "failed")

        await self.store.append(
            "session-response-terminal",
            [ModelResponse(parts=[TextPart(content="partial")], run_id="run-partial")],
            agent_id=self.agent_id,
            user_id="user-1",
            turn_id="run-partial",
            run_id="run-partial",
            terminal_status="interrupted",
        )
        suppressed = await self.store.append_terminal(
            "session-response-terminal",
            status="interrupted",
            agent_id=self.agent_id,
            user_id="user-1",
            turn_id="run-partial",
            run_id="run-partial",
            payload={"reason": "cancelled"},
        )
        self.assertIsNone(suppressed)
        events = await self.store.list_events("session-response-terminal")
        self.assertEqual([event.event_type for event in events], ["assistant/message"])

    async def test_pre_persisted_user_and_run_tail_keep_message_ordinals(self) -> None:
        user = ModelRequest(parts=[UserPromptPart(content="question")], run_id="run-split")
        response = ModelResponse(parts=[TextPart(content="answer")], run_id="run-split")
        await self.store.append(
            "session-split",
            [user],
            agent_id=self.agent_id,
            user_id="user-1",
            turn_id="e" * 32,
            run_id="run-split",
            first_message_ordinal=0,
        )
        await self.store.append(
            "session-split",
            [response],
            agent_id=self.agent_id,
            user_id="user-1",
            turn_id="e" * 32,
            run_id="run-split",
            first_message_ordinal=1,
        )
        self.assertEqual(
            await self.store.get_raw_history("session-split", agent_id=self.agent_id),
            [user, response],
        )

    async def test_sqlite_concurrent_compaction_sequence_allocation_is_unique(self) -> None:
        async def append_run(index: int):
            return await self.store.append_lifecycle(
                "session-concurrent",
                event_type="compaction",
                status="running",
                agent_id=self.agent_id,
                user_id="user-1",
                turn_id="f" * 32,
                run_id=f"run-{index}",
                payload={"index": index},
            )

        rows = await asyncio.gather(*(append_run(index) for index in range(20)))
        self.assertEqual(sorted(row.sequence for row in rows), list(range(1, 21)))
        events = await self.store.list_events("session-concurrent", limit=100)
        self.assertEqual([event.sequence for event in events], list(range(1, 21)))

    async def test_completed_compaction_replaces_only_prefix_surface(self) -> None:
        raw = [
            ModelRequest(
                parts=[
                    UserPromptPart(
                        content=[
                            "first",
                            {
                                "kind": "attachment-ref",
                                "id": "local_123",
                                "name": "预算.xlsx",
                                "mime": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            },
                        ]
                    )
                ]
            ),
            ModelResponse(parts=[TextPart(content="old answer")]),
            ModelRequest(parts=[UserPromptPart(content="latest")]),
            ModelResponse(parts=[TextPart(content="latest answer")]),
        ]
        await self.store.append(
            "session-3", raw, agent_id=self.agent_id, user_id="user-1",
            turn_id="c" * 32, run_id="run-3",
        )
        summary = "## 1. Primary Request and Intent\nfirst\n\n" + "\n\n".join(
            f"## {index}. {title}\n无"
            for index, title in (
                (2, "Key Technical Concepts"), (3, "Files and Code"),
                (4, "Errors and Fixes"), (5, "Pending Jobs"),
                (6, "Current Work"), (7, "Next Step"), (8, "Critical Context"),
            )
        )
        projected = [
            ModelRequest(parts=[UserPromptPart(content=f"Summary of previous conversation:\n\n{summary}")]),
            *raw[-2:],
        ]
        event = await self.store.persist_compaction_projection(
            "session-3",
            agent_id=self.agent_id,
            user_id="user-1",
            raw_history=raw,
            projected_history=projected,
            summary_markdown=summary,
            trigger="manual",
            model_id="model",
            estimated_before=100,
            estimated_after=50,
            target_tokens=60,
            context_window_tokens=1000,
            preserved_tail_tokens=25,
            duration_ms=10,
            summary_call={
                "provider_id": "test-provider",
                "model_id": "resolved-model",
                "finish_reason": "stop",
                "usage": {"input_tokens": 80, "output_tokens": 20},
                "max_output_tokens": 512,
            },
        )

        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual(event.payload["budget"]["preserved_tail_tokens"], 25)
        self.assertEqual(event.payload["summary_call"]["model_id"], "model")
        attachment_evidence = event.payload["replacement"]["attachment_evidence"]
        self.assertEqual(attachment_evidence[0]["file_id"], "local_123")
        self.assertEqual(attachment_evidence[0]["part_offset"], 0)
        self.assertEqual(attachment_evidence[0]["item_offset"], 1)
        self.assertEqual(
            event.payload["summary_call"]["resolved_model_name"], "resolved-model"
        )
        visible = await self.store.get_history("session-3", agent_id=self.agent_id)
        self.assertEqual(len(visible), 3)
        self.assertTrue(
            visible[0].parts[0].content.startswith("Summary of previous conversation:")
        )
        self.assertIn("DeepAgent deterministic evidence", visible[0].parts[0].content)
        self.assertEqual(visible[-2:], raw[-2:])
        self.assertEqual(
            await self.store.get_raw_history("session-3", agent_id=self.agent_id),
            raw,
        )


if __name__ == "__main__":
    unittest.main()
