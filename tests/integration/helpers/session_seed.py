from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.event_codec import encode_messages
from deep_agent.db.ids import new_event_id, new_turn_id
from deep_agent.db.models import Agent, ChatSession, ChatSessionEvent, LlmModel, LlmProvider
from deep_agent.db.serialization import deserialize_message
from deep_agent.db.stores.bundle import DataStores
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.llm_model import LlmModelCreate
from tests.helpers.request_identity import TEST_USER_ID
from tests.helpers.sqlite_stores import (
    DEFAULT_TEST_CONTEXT_WINDOW,
    DEFAULT_TEST_MAX_OUTPUT,
    get_default_test_provider_id,
)


@dataclass(frozen=True)
class SeededSession:
    session_id: str
    agent_id: str
    model_id: str


def load_session_fixture(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


async def seed_llm_model(
    stores: DataStores,
    *,
    name: str,
    model: str,
    model_id: str | None = None,
    input_modalities: list[str] | None = None,
) -> str:
    row = await stores.llm_configuration.create_model(
        LlmModelCreate(
            provider_id=await get_default_test_provider_id(stores),
            name=name,
            model=model,
            input_modalities=input_modalities or ["text"],
            context_window_tokens=DEFAULT_TEST_CONTEXT_WINDOW,
            max_output_tokens=DEFAULT_TEST_MAX_OUTPUT,
        )
    )
    if model_id is not None or input_modalities is not None:
        async with stores.llm_model._write() as db:  # noqa: SLF001
            db_row = await stores.llm_model._get_by_id(db, row.id)  # noqa: SLF001
            assert db_row is not None
            if model_id is not None:
                db_row.id = model_id
            if input_modalities is not None:
                db_row.input_modalities = input_modalities
            await db.flush()
            await db.refresh(db_row)
            return db_row.id
    return row.id


async def seed_agent(
    stores: DataStores,
    *,
    agent_id: str,
    model_id: str,
    name: str = "integration-agent",
    prompt: str = "You are a helpful assistant.",
) -> str:
    row = await stores.agent.create_unique(
        AgentCreate(name=name, model_id=model_id, prompt=prompt),
    )
    if row.id != agent_id:
        async with stores.agent._write() as db:  # noqa: SLF001
            db_row = await stores.agent._get_by_id(db, row.id)  # noqa: SLF001
            assert db_row is not None
            db_row.id = agent_id
            await db.flush()
    return agent_id


async def seed_session_from_fixture(
    session_factory: async_sessionmaker[AsyncSession],
    fixture: dict,
    *,
    agent_id: str | None = None,
    model_id: str | None = None,
    model_name: str = "integration-model",
    model: str = "test:integration-model",
) -> SeededSession:
    resolved_model_id = model_id or "11111111-2222-3333-4444-555555555555"
    resolved_agent_id = agent_id or fixture["agent_id"]
    session_id = fixture["session_id"]
    title = fixture.get("title")

    async with session_factory() as db:
        async with db.begin():
            provider = (await db.execute(select(LlmProvider).order_by(LlmProvider.id))).scalars().first()
            if provider is None:
                raise AssertionError("fixture seed requires a pre-seeded LLM provider")
            db.add(
                LlmModel(
                    id=resolved_model_id,
                    provider_id=provider.id,
                    name=model_name,
                    model=model,
                    input_modalities=["text"],
                    context_window_tokens=DEFAULT_TEST_CONTEXT_WINDOW,
                    max_output_tokens=DEFAULT_TEST_MAX_OUTPUT,
                    tags=[],
                )
            )
            db.add(
                Agent(
                    id=resolved_agent_id,
                    name="fixture-agent",
                    model_id=resolved_model_id,
                    prompt="fixture prompt",
                )
            )

            now = datetime.now(timezone.utc)
            db.add(
                ChatSession(
                    session_id=session_id,
                    agent_id=resolved_agent_id,
                    user_id=TEST_USER_ID,
                    title=title,
                    next_sequence=1,
                    created_at=now,
                    updated_at=now,
                )
            )
            await db.flush()
            messages = [
                deserialize_message(item["payload"])
                for item in sorted(
                    fixture.get("messages", []), key=lambda item: int(item["sequence"])
                )
            ]
            turn_id = new_turn_id()
            drafts = encode_messages(
                messages,
                session_id=session_id,
                run_id="fixture-import",
            )
            for sequence, draft in enumerate(drafts, start=1):
                db.add(
                    ChatSessionEvent(
                        event_id=new_event_id(),
                        session_id=session_id,
                        sequence=sequence,
                        schema_version=draft.schema_version,
                        event_type=draft.event_type,
                        status=None,
                        turn_id=turn_id,
                        run_id="fixture-import",
                        message_id=draft.message_id,
                        part_index=draft.part_index,
                        call_id=draft.call_id,
                        tool_name=draft.tool_name,
                        search_text=draft.search_text,
                        payload=draft.payload,
                        created_at=now,
                        updated_at=now,
                    )
                )
            session = await db.get(ChatSession, session_id)
            assert session is not None
            session.next_sequence = len(drafts) + 1

    return SeededSession(
        session_id=session_id,
        agent_id=resolved_agent_id,
        model_id=resolved_model_id,
    )


async def wait_for_session_title(
    store,
    session_id: str,
    *,
    agent_id: str | None,
    timeout: float = 15.0,
    interval: float = 0.25,
) -> str | None:
    deadline = time.monotonic() + timeout
    last: str | None = None
    while time.monotonic() < deadline:
        last = await store.get_session_title(session_id, agent_id=agent_id)
        if last:
            return last
        await asyncio.sleep(interval)
    return last
