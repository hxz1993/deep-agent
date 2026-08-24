from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.agent.provider_registry import (
    UnknownProviderTypeError,
    provider_type_definition,
)
from deep_agent.db.models import Agent, AuxiliaryModelConfig, LlmModel, LlmProvider
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.db.stores.llm_model.base import _validate_runtime_metadata
from deep_agent.schemas.llm_model import LlmModelCreate, LlmModelUpdate
from deep_agent.schemas.llm_provider import LlmProviderCreate, LlmProviderUpdate


class LlmConfigurationConflictError(ValueError):
    pass


class LlmConfigurationValidationError(ValueError):
    pass


def _is_unique_constraint_error(exc: IntegrityError) -> bool:
    message = str(exc.orig).lower()
    return any(
        marker in message
        for marker in (
            "unique constraint",
            "duplicate entry",
            "duplicate key",
        )
    )


def resolve_provider_base_url(
    *,
    provider_type: str,
    use_default_base_url: bool,
    base_url: str | None,
) -> str:
    try:
        definition = provider_type_definition(provider_type)
    except UnknownProviderTypeError as exc:
        raise LlmConfigurationValidationError(
            f"Unsupported provider_type: {provider_type}"
        ) from exc
    if use_default_base_url:
        if not definition.supports_default_base_url or definition.default_base_url is None:
            raise LlmConfigurationValidationError(
                f"Provider type {provider_type} does not define a default base URL"
            )
        return definition.default_base_url.rstrip("/")
    if not definition.supports_custom_base_url:
        raise LlmConfigurationValidationError(
            f"Provider type {provider_type} does not support a custom base URL"
        )
    if not base_url:
        raise LlmConfigurationValidationError(
            "base_url is required when use_default_base_url is false"
        )
    return base_url.strip().rstrip("/")


class LlmConfigurationService(SessionBoundMixin):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession] | None) -> None:
        super().__init__(session_factory)

    async def list_providers(self) -> list[LlmProvider]:
        async with self._read() as db:
            result = await db.execute(select(LlmProvider).order_by(LlmProvider.name))
            return list(result.scalars().all())

    async def get_provider(self, provider_id: str) -> LlmProvider | None:
        async with self._read() as db:
            return await db.get(LlmProvider, provider_id)

    async def get_provider_by_name(self, name: str) -> LlmProvider | None:
        async with self._read() as db:
            result = await db.execute(select(LlmProvider).where(LlmProvider.name == name))
            return result.scalar_one_or_none()

    async def count_provider_models(self, provider_id: str) -> int:
        async with self._read() as db:
            return await self._count_provider_models(db, provider_id)

    async def create_provider(self, data: LlmProviderCreate) -> LlmProvider:
        try:
            async with self._write() as db:
                existing = await db.execute(
                    select(LlmProvider.id).where(LlmProvider.name == data.name)
                )
                if existing.scalar_one_or_none() is not None:
                    raise LlmConfigurationConflictError(
                        f"LLM provider already exists: {data.name}"
                    )
                base_url = resolve_provider_base_url(
                    provider_type=data.provider_type,
                    use_default_base_url=data.use_default_base_url,
                    base_url=data.base_url,
                )
                row = LlmProvider(
                    name=data.name,
                    provider_type=data.provider_type,
                    use_default_base_url=data.use_default_base_url,
                    base_url=base_url,
                    api_key=data.api_key,
                    enabled=data.enabled,
                )
                db.add(row)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if _is_unique_constraint_error(exc):
                raise LlmConfigurationConflictError(
                    f"LLM provider already exists: {data.name}"
                ) from exc
            raise

    async def update_provider(
        self,
        provider_id: str,
        data: LlmProviderUpdate,
    ) -> LlmProvider | None:
        try:
            async with self._write() as db:
                row = await db.get(LlmProvider, provider_id)
                if row is None:
                    return None
                updates = data.model_dump(exclude_unset=True)
                clear_api_key = bool(updates.pop("clear_api_key", False))
                if clear_api_key:
                    updates["api_key"] = None
                next_type = str(updates.get("provider_type", row.provider_type))
                if next_type != row.provider_type:
                    if await self._count_provider_models(db, provider_id):
                        raise LlmConfigurationConflictError(
                            "Provider type cannot change while models reference it"
                        )
                if "name" in updates and updates["name"] != row.name:
                    existing = await db.execute(
                        select(LlmProvider.id).where(LlmProvider.name == updates["name"])
                    )
                    if existing.scalar_one_or_none() is not None:
                        raise LlmConfigurationConflictError(
                            f"LLM provider already exists: {updates['name']}"
                        )
                next_default = bool(
                    updates.get("use_default_base_url", row.use_default_base_url)
                )
                next_base_url = resolve_provider_base_url(
                    provider_type=next_type,
                    use_default_base_url=next_default,
                    base_url=updates.get("base_url", row.base_url),
                )
                for field, value in updates.items():
                    if field != "base_url":
                        setattr(row, field, value)
                row.base_url = next_base_url
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if _is_unique_constraint_error(exc):
                conflict_name = data.name if "name" in data.model_fields_set else provider_id
                raise LlmConfigurationConflictError(
                    f"LLM provider already exists: {conflict_name}"
                ) from exc
            raise

    async def delete_provider(self, provider_id: str) -> bool:
        async with self._write() as db:
            if await self._count_provider_models(db, provider_id):
                raise LlmConfigurationConflictError(
                    "LLM provider is still referenced by one or more models"
                )
            result = await db.execute(
                delete(LlmProvider).where(LlmProvider.id == provider_id)
            )
            return bool(result.rowcount)

    async def create_model(self, data: LlmModelCreate) -> LlmModel:
        try:
            async with self._write() as db:
                provider = await db.get(LlmProvider, data.provider_id)
                if provider is None:
                    raise LlmConfigurationValidationError(
                        f"LLM provider not found: {data.provider_id}"
                    )
                existing = await db.execute(
                    select(LlmModel.id).where(
                        LlmModel.provider_id == data.provider_id,
                        LlmModel.name == data.name,
                    )
                )
                if existing.scalar_one_or_none() is not None:
                    raise LlmConfigurationConflictError(
                        "LLM model already exists for provider: "
                        f"{data.provider_id}/{data.name}"
                    )
                row = LlmModel(
                    provider_id=provider.id,
                    name=data.name,
                    description=data.description,
                    model=data.model,
                    input_modalities=data.input_modalities,
                    context_window_tokens=data.context_window_tokens,
                    max_output_tokens=data.max_output_tokens,
                    tags=data.tags,
                    enabled=data.enabled,
                )
                db.add(row)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if _is_unique_constraint_error(exc):
                raise LlmConfigurationConflictError(
                    "LLM model already exists for provider: "
                    f"{data.provider_id}/{data.name}"
                ) from exc
            raise

    async def update_model(
        self,
        model_id: str,
        data: LlmModelUpdate,
    ) -> LlmModel | None:
        try:
            async with self._write() as db:
                row = await db.get(LlmModel, model_id)
                if row is None:
                    return None
                updates = data.model_dump(exclude_unset=True)
                provider_id = str(updates.get("provider_id", row.provider_id))
                provider = await db.get(LlmProvider, provider_id)
                if provider is None:
                    raise LlmConfigurationValidationError(
                        f"LLM provider not found: {provider_id}"
                    )
                next_name = str(updates.get("name", row.name))
                if provider_id != row.provider_id or next_name != row.name:
                    existing = await db.execute(
                        select(LlmModel.id).where(
                            LlmModel.provider_id == provider_id,
                            LlmModel.name == next_name,
                            LlmModel.id != model_id,
                        )
                    )
                    if existing.scalar_one_or_none() is not None:
                        raise LlmConfigurationConflictError(
                            "LLM model already exists for provider: "
                            f"{provider_id}/{next_name}"
                        )
                for field, value in updates.items():
                    setattr(row, field, value)
                _validate_runtime_metadata(row)
                await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if _is_unique_constraint_error(exc):
                raise LlmConfigurationConflictError(
                    f"LLM model write conflict: {model_id}"
                ) from exc
            raise

    async def delete_model(self, model_id: str) -> bool:
        async with self._write() as db:
            row = await db.get(LlmModel, model_id)
            if row is None:
                return False
            agent_count = await db.execute(
                select(func.count()).select_from(Agent).where(Agent.model_id == model_id)
            )
            auxiliary_count = await db.execute(
                select(func.count()).select_from(AuxiliaryModelConfig).where(
                    AuxiliaryModelConfig.llm_model_id == model_id
                )
            )
            reference_count = int(agent_count.scalar_one()) + int(
                auxiliary_count.scalar_one()
            )
            if reference_count:
                raise LlmConfigurationConflictError(
                    f"LLM model is referenced by {reference_count} configuration(s)"
                )
            await db.delete(row)
            await db.flush()
            return True

    async def _count_provider_models(self, db: AsyncSession, provider_id: str) -> int:
        result = await db.execute(
            select(func.count()).select_from(LlmModel).where(
                LlmModel.provider_id == provider_id
            )
        )
        return int(result.scalar_one())
