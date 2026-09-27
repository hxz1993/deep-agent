from __future__ import annotations

from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.models.provider_registry import (
    UnknownProviderTypeError,
    provider_type_definition,
)
from deep_agent.db.models import Agent, GlobalSetting, LlmModel, LlmProvider
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.db.reference_integrity import lock_optional, is_unique_conflict
from deep_agent.db.stores.llm_model.base import _validate_runtime_metadata
from deep_agent.schemas.llm_model import LlmModelCreate, LlmModelUpdate
from deep_agent.schemas.llm_provider import LlmProviderCreate, LlmProviderUpdate
from deep_agent.services.codex_provider import credential_payload, require_codex_enabled, evict_provider
from pydantic_ai.providers.openai_codex import OpenAICodexCredentials
from deep_agent.web.settings import (
    WEB_SEARCH_SETTING_KEY,
    WebSearchConfigurationError,
    resolve_web_search_config,
)


class LlmConfigurationConflictError(ValueError):
    pass


class LlmConfigurationValidationError(ValueError):
    pass


def _is_unique_constraint_error(exc: IntegrityError) -> bool:
    return is_unique_conflict(exc)


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
            result = await db.execute(select(LlmProvider).order_by(LlmProvider.created_at, LlmProvider.id))
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

    async def disconnect_provider(self, provider_id: str) -> LlmProvider | None:
        async with self._write() as db:
            row = await db.get(LlmProvider, provider_id, with_for_update=True)
            if row is None:
                return None
            if row.provider_type != "openai_codex" or row.auth_method != "oauth":
                raise LlmConfigurationValidationError("仅订阅供应商可以断开授权")
            row.credential_json = None
            row.auth_status = "disconnected"
            row.auth_revision += 1
            await db.flush()
            await db.refresh(row)
            evict_provider(provider_id)
            return row

    async def create_provider(self, data: LlmProviderCreate, *, codex_credentials: OpenAICodexCredentials | None = None) -> LlmProvider:
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
                if data.provider_type == "openai_codex":
                    require_codex_enabled()
                    if codex_credentials is None or data.api_key or not data.use_default_base_url:
                        raise LlmConfigurationValidationError("Codex 订阅必须完成授权且使用默认地址")
                elif codex_credentials is not None:
                    raise LlmConfigurationValidationError("API Provider 不能使用订阅凭据")
                row = LlmProvider(
                    name=data.name,
                    provider_type=data.provider_type,
                    use_default_base_url=data.use_default_base_url,
                    base_url=base_url,
                    api_key=data.api_key,
                    auth_method="oauth" if codex_credentials else "api_key",
                    credential_json=credential_payload(codex_credentials) if codex_credentials else None,
                    auth_status="connected" if codex_credentials else "unconfigured",
                    auth_revision=1 if codex_credentials else 0,
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
        *, codex_credentials: OpenAICodexCredentials | None = None,
    ) -> LlmProvider | None:
        try:
            async with self._write() as db:
                row = await db.get(LlmProvider, provider_id, with_for_update=True)
                if row is None:
                    return None
                updates = data.model_dump(exclude_unset=True)
                updates.pop("authorization_flow_id", None)
                if row.provider_type == "openai_codex":
                    if any(field in updates for field in ("provider_type", "api_key", "clear_api_key", "base_url", "use_default_base_url")):
                        raise LlmConfigurationValidationError("订阅供应商只能修改名称或重新授权")
                    if codex_credentials is not None:
                        require_codex_enabled()
                        row.credential_json = credential_payload(codex_credentials)
                        row.auth_revision += 1
                        row.auth_status = "connected"
                        evict_provider(provider_id)
                elif codex_credentials is not None or updates.get("provider_type") == "openai_codex":
                    raise LlmConfigurationValidationError("请新建订阅供应商并完成授权")
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
        try:
            async with self._write() as db:
                provider = await db.get(LlmProvider, provider_id, with_for_update=True)
                if provider is None:
                    return False
                # Freeze the model set before checking Agent references. Model
                # creation/moves lock this Provider; Agent writes lock the Model.
                await db.execute(select(LlmModel.id).where(
                    LlmModel.provider_id == provider_id
                ).order_by(LlmModel.id).with_for_update())
                agents = await db.execute(
                    select(Agent.name)
                    .join(LlmModel, Agent.model_id == LlmModel.id)
                    .where(LlmModel.provider_id == provider_id)
                    .order_by(Agent.id).with_for_update()
                )
                names = list(agents.scalars())
                if names:
                    raise LlmConfigurationConflictError(
                        "无法删除：模型仍被 Agent「"
                        + "、".join(names)
                        + "」使用，请先更换这些 Agent 的模型。"
                    )
                setting = await db.get(GlobalSetting, WEB_SEARCH_SETTING_KEY)
                if setting is not None:
                    try:
                        search = resolve_web_search_config(setting.value, require_credentials=False)
                    except WebSearchConfigurationError:
                        search = None
                    if search is not None and search.llm_provider_id == provider_id:
                        raise LlmConfigurationConflictError(
                            "无法删除：全局 Web Search 正在使用此 Provider 的凭据，请先更换搜索配置。"
                        )
                # Delete this Provider and its unreferenced models atomically.
                await db.execute(delete(LlmModel).where(LlmModel.provider_id == provider_id))
                await db.delete(provider)
                evict_provider(provider_id)
                await db.flush()
                return True
        except IntegrityError as exc:
            # A manually retained legacy reference/trigger is an expected
            # conflict. NOT NULL, CHECK and other persistence failures are not.
            orig = exc.orig
            code = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
            args = getattr(orig, "args", ())
            if code == "23503" or (args and args[0] in (1451, 1452)) or "foreign key constraint failed" in str(orig).lower():
                raise LlmConfigurationConflictError(
                    "无法删除：Provider 或模型仍存在配置引用，请刷新后检查引用关系。"
                ) from exc
            raise

    async def create_model(self, data: LlmModelCreate) -> LlmModel:
        try:
            async with self._write() as db:
                provider = await db.get(LlmProvider, data.provider_id, with_for_update=True)
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
                    purpose=data.purpose,
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
                old_provider = row.provider_id
                parents = {}
                for parent_id in sorted({old_provider, provider_id}):
                    parents[parent_id] = await db.get(LlmProvider, parent_id, with_for_update=True)
                row = await lock_optional(db, LlmModel, LlmModel.id, model_id)
                if row is None:
                    return None
                if row.provider_id != old_provider:
                    raise LlmConfigurationConflictError("Model Provider changed concurrently; refresh and retry")
                provider = parents[provider_id]
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
            row = await lock_optional(db, LlmModel, LlmModel.id, model_id)
            if row is None:
                return False
            agent_count = await db.execute(
                select(Agent.id).where(Agent.model_id == model_id).order_by(Agent.id).with_for_update()
            )
            reference_count = len(agent_count.all())
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
