from __future__ import annotations

import asyncio
import os

from deep_agent.db.stores.bundle import DataStores
from deep_agent.schemas.auxiliary_model import AuxiliaryModelUpdate
from deep_agent.schemas.llm_model import LlmModelCreate

DEFAULT_TEST_LLM_MODEL = "test-default"
DEFAULT_TEST_LLM_MODEL_STRING = "test"
AUX_TITLE_MODEL_NAME = "aux-title"
DEFAULT_TEST_CONTEXT_WINDOW = 128_000
DEFAULT_TEST_MAX_OUTPUT = 8_192


def test_llm_model_create(*, name: str, model: str, **kwargs) -> LlmModelCreate:
    return LlmModelCreate(
        name=name,
        model=model,
        input_modalities=kwargs.pop("input_modalities", ["text"]),
        context_window_tokens=kwargs.pop(
            "context_window_tokens", DEFAULT_TEST_CONTEXT_WINDOW
        ),
        max_output_tokens=kwargs.pop(
            "max_output_tokens", DEFAULT_TEST_MAX_OUTPUT
        ),
        **kwargs,
    )


async def seed_default_llm_model(stores: DataStores) -> None:
    """Ensure chat integration tests can resolve the default DB-backed model."""
    if await stores.llm_model.list_all():
        return
    await stores.llm_model.create_unique(
        test_llm_model_create(
            name=DEFAULT_TEST_LLM_MODEL,
            model=DEFAULT_TEST_LLM_MODEL_STRING,
        ),
    )


async def ensure_title_uses_main_model(stores: DataStores) -> None:
    """Force title generation to auto (main model) for non-title integration tests."""
    await stores.auxiliary_model.ensure_defaults()
    await stores.auxiliary_model.update(
        "title",
        AuxiliaryModelUpdate(llm_model_id=None),
    )


async def seed_auxiliary_model_configs(stores: DataStores) -> None:
    """Ensure auxiliary model rows exist and mirror DEEP_AGENT_TITLE_* env for tests."""
    await stores.auxiliary_model.ensure_defaults()
    model_str = os.environ.get("DEEP_AGENT_TITLE_MODEL", "").strip()
    base_url = os.environ.get("DEEP_AGENT_TITLE_MODEL_BASE_URL", "").strip() or None
    api_key = os.environ.get("DEEP_AGENT_TITLE_MODEL_API_KEY", "").strip() or None

    llm_model_id = None
    if model_str:
        existing = await stores.llm_model.get_by_name(AUX_TITLE_MODEL_NAME)
        if existing is not None:
            llm_model_id = existing.id
        else:
            row = await stores.llm_model.create_unique(
                test_llm_model_create(
                    name=AUX_TITLE_MODEL_NAME,
                    model=model_str,
                    base_url=base_url,
                    api_key=api_key,
                ),
            )
            llm_model_id = row.id

    await stores.auxiliary_model.update(
        "title",
        AuxiliaryModelUpdate(llm_model_id=llm_model_id),
    )


async def make_sqlite_stores(
    url: str = "sqlite+aiosqlite:///:memory:",
) -> tuple[DataStores, object, object]:
    from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

    from deep_agent.db.dialect import DbDialect
    from deep_agent.db.models import Base
    from deep_agent.db.stores.bundle import create_data_stores

    engine = create_async_engine(url, pool_pre_ping=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, expire_on_commit=False)
    stores = create_data_stores(DbDialect.SQLITE, session_factory)
    await seed_default_llm_model(stores)
    await seed_auxiliary_model_configs(stores)
    return stores, engine, session_factory


def make_sqlite_stores_sync(
    url: str = "sqlite+aiosqlite:///:memory:",
) -> tuple[DataStores, object, object]:
    return asyncio.run(make_sqlite_stores(url))
