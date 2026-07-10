from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.models import Agent, ChatMessage, ChatSession, LlmModel
from deep_agent.db.serialization import deserialize_message, serialize_message
from deep_agent.db.session_scope import normalize_agent_scope
from deep_agent.db.stores.bundle import DataStores
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.llm_model import LlmModelCreate


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
    capabilities_source: str | None = None,
) -> str:
    row = await stores.llm_model.create_unique(
        LlmModelCreate(
            name=name,
            model=model,
            input_modalities=input_modalities or [],
        )
    )
    if model_id is not None or input_modalities is not None or capabilities_source is not None:
        async with stores.llm_model._write() as db:  # noqa: SLF001
            db_row = await stores.llm_model._get_by_id(db, row.id)  # noqa: SLF001
            assert db_row is not None
            if model_id is not None:
                db_row.id = model_id
            if input_modalities is not None:
                db_row.input_modalities = input_modalities
            if capabilities_source is not None:
                db_row.capabilities_source = capabilities_source
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
            db.add(
                LlmModel(
                    id=resolved_model_id,
                    name=model_name,
                    model=model,
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
            scope = normalize_agent_scope(resolved_agent_id)
            db.add(
                ChatSession(
                    agent_id=scope,
                    session_id=session_id,
                    title=title,
                    created_at=now,
                    updated_at=now,
                )
            )
            for item in fixture.get("messages", []):
                payload = item["payload"]
                message = deserialize_message(payload)
                db.add(
                    ChatMessage(
                        agent_id=scope,
                        session_id=session_id,
                        sequence=int(item["sequence"]),
                        payload=serialize_message(message),
                    )
                )

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
