from __future__ import annotations

import asyncio
from deep_agent.db.stores.bundle import DataStores
from deep_agent.schemas.agent import AgentCreate
from deep_agent.schemas.llm_model import LlmModelCreate
from deep_agent.schemas.llm_provider import LlmProviderCreate

DEFAULT_TEST_LLM_MODEL = "test-default"
DEFAULT_TEST_LLM_MODEL_STRING = "test"
DEFAULT_TEST_CONTEXT_WINDOW = 128_000
DEFAULT_TEST_MAX_OUTPUT = 8_192
DEFAULT_TEST_PROVIDER_NAME = "Test Provider"


def test_llm_model_create(*, name: str, model: str, **kwargs) -> LlmModelCreate:
    return LlmModelCreate(
        provider_id=kwargs.pop("provider_id"),
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
    provider = await stores.llm_configuration.get_provider_by_name(
        DEFAULT_TEST_PROVIDER_NAME
    )
    if provider is None:
        provider = await stores.llm_configuration.create_provider(
            LlmProviderCreate(
                name=DEFAULT_TEST_PROVIDER_NAME,
                provider_type="openai_compatible",
                base_url="http://test.invalid/v1",
                api_key="test-key",
            )
        )
    await stores.llm_configuration.create_model(
        test_llm_model_create(
            name=DEFAULT_TEST_LLM_MODEL,
            model=DEFAULT_TEST_LLM_MODEL_STRING,
            provider_id=provider.id,
        ),
    )


async def seed_default_agent(
    stores: DataStores,
    *,
    name: str = "integration-agent",
) -> str:
    """Create an explicit Agent for chat integration tests."""
    models = await stores.llm_model.list_all()
    if not models:
        raise AssertionError("default test model was not seeded")
    row = await stores.agent.create_unique(
        AgentCreate(
            name=name,
            model_id=models[0].id,
            prompt="You are a helpful integration-test assistant.",
        )
    )
    return row.id


async def get_default_test_provider_id(stores: DataStores) -> str:
    provider = await stores.llm_configuration.get_provider_by_name(
        DEFAULT_TEST_PROVIDER_NAME
    )
    if provider is None:
        raise AssertionError("default test provider was not seeded")
    return provider.id


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
    return stores, engine, session_factory


def make_sqlite_stores_sync(
    url: str = "sqlite+aiosqlite:///:memory:",
) -> tuple[DataStores, object, object]:
    return asyncio.run(make_sqlite_stores(url))
