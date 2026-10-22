from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import delete, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from deep_agent.db.models import Agent, AgentOutputConfig, LlmModel, UiToolPackage, ChatSession, ChatSessionEvent, ChannelAccount, ChannelSessionBinding, McpServer
from deep_agent.db.deletion import DeletionImpact, DeletionConfirmationRequired
from deep_agent.db.reference_integrity import ReferenceConflict, lock_reference, lock_optional, is_unique_conflict
from deep_agent.db.stores._session import SessionBoundMixin
from deep_agent.schemas.agent import AgentCreate, AgentUpdate, AgentOutputConfig as OutputConfig


class InvalidAgentOutputConfig(ValueError):
    """The merged Agent/output state is invalid, rather than a name conflict."""


def trusted_updater(user_id: str | None) -> str | None:
    if user_id is None:
        return None
    if not user_id.strip() or len(user_id.strip()) > 128:
        raise InvalidAgentOutputConfig("authenticated operator id must contain 1 to 128 characters")
    return user_id.strip()


async def save_output_config(
    db: AsyncSession, agent_id: str, config: OutputConfig | None, *,
    allow_text_output: bool | None = None,
) -> None:
    """Caller holds the parent Agent lock/configuration write barrier."""
    current = await db.get(AgentOutputConfig, agent_id)
    if config is None:
        if allow_text_output:
            raise InvalidAgentOutputConfig("output_config is required when allow_text_output is true")
        if current is not None:
            await db.delete(current)
        return
    values = config.model_dump()
    if allow_text_output is not None:
        values["allow_text_output"] = allow_text_output
    if current is None:
        db.add(AgentOutputConfig(agent_id=agent_id, **values))
    else:
        for key, value in values.items():
            setattr(current, key, value)
        current.updated_at = datetime.now(UTC)


class AgentStoreBase(SessionBoundMixin):
    async def get_by_id(self, agent_id: str) -> Agent | None:
        async with self._read() as db:
            return await self._get_by_id(db, agent_id)

    async def get_by_name(self, name: str) -> Agent | None:
        async with self._read() as db:
            return await self._get_by_name(db, name)

    async def list_all(self) -> list[Agent]:
        async with self._read() as db:
            return await self._list_all(db)

    async def get_output_config(self, agent_id: str) -> AgentOutputConfig | None:
        async with self._read() as db:
            return await db.get(AgentOutputConfig, agent_id)

    async def get_output_configuration(self, agent_id: str) -> tuple[Agent | None, AgentOutputConfig | None]:
        """One statement gives management a consistent switch/definition pair."""
        async with self._read() as db:
            result = (await db.execute(select(Agent, AgentOutputConfig).outerjoin(
                AgentOutputConfig, AgentOutputConfig.agent_id == Agent.id,
            ).where(Agent.id == agent_id))).first()
            return (result[0], result[1]) if result is not None else (None, None)

    async def create_unique(self, data: AgentCreate, *, user_id: str | None = None) -> Agent:
        try:
            async with self._write() as db:
                if data.model_id is not None:
                    await lock_reference(db, LlmModel, LlmModel.id, data.model_id, "LLM model")
                await self._lock_ui_packages(db, data.capabilities)
                await self._lock_mcp_servers(db, data.capabilities)
                if await self._get_by_name(db, data.name) is not None:
                    raise ValueError(f"Agent already exists: {data.name}")
                row = Agent(
                    name=data.name,
                    model_id=data.model_id,
                    capabilities=data.capabilities,
                    ui_tool_selection={},
                    skills=data.skills,
                    prompt=data.prompt,
                    structured_output_enabled=data.structured_output_enabled,
                    updated_by=trusted_updater(user_id),
                )
                db.add(row)
                await db.flush()
                if data.output_config is not None:
                    await save_output_config(db, row.id, data.output_config, allow_text_output=data.allow_text_output)
                    await db.flush()
                await db.refresh(row)
                return row
        except IntegrityError as exc:
            if not is_unique_conflict(exc):
                raise
            raise ValueError(f"Agent write conflict: {data.name}") from exc

    async def update_unique(self, agent_id: str, data: AgentUpdate, *, user_id: str | None = None) -> Agent | None:
        try:
            async with self._write() as db:
                row = await self._get_by_id(db, agent_id)
                if row is None:
                    return None
                old_model, old_caps = row.model_id, list(row.capabilities or [])
                new_model = data.model_id if "model_id" in data.model_fields_set else old_model
                for model_id in sorted(value for value in {old_model, new_model} if value is not None):
                    await lock_reference(db, LlmModel, LlmModel.id, model_id, "LLM model")
                await self._lock_ui_packages(db, old_caps + (data.capabilities or []))
                await self._lock_mcp_servers(db, data.capabilities if data.capabilities is not None else old_caps,
                                             old_capabilities=old_caps)
                row = await lock_optional(db, Agent, Agent.id, agent_id)
                if row is None:
                    return None
                if row.model_id != old_model or list(row.capabilities or []) != old_caps:
                    raise ReferenceConflict("Agent references changed concurrently; refresh and retry")
                if data.name is not None and data.name != row.name:
                    conflict = await self._get_by_name(db, data.name)
                    if conflict is not None:
                        raise ValueError(f"Agent already exists: {data.name}")
                enabled = (data.structured_output_enabled if "structured_output_enabled" in data.model_fields_set
                           else row.structured_output_enabled)
                definition_changed = "output_config" in data.model_fields_set
                policy_changed = "allow_text_output" in data.model_fields_set
                # MySQL's initial ordinary Agent read may have established a
                # repeatable-read snapshot before we waited for the parent.
                # Read current config facts after acquiring that parent lock.
                existing_config = await lock_optional(db, AgentOutputConfig, AgentOutputConfig.agent_id, agent_id)
                if definition_changed:
                    config = data.output_config
                elif enabled or policy_changed:
                    try:
                        config = OutputConfig.model_validate(existing_config) if existing_config is not None else None
                    except ValueError as exc:
                        raise InvalidAgentOutputConfig("saved output_config is invalid") from exc
                else:
                    # Preserve even a disabled legacy draft during unrelated edits.
                    config = None
                allow_text = (data.allow_text_output if policy_changed else
                              bool(existing_config.allow_text_output) if existing_config is not None else False)
                if definition_changed and config is None and not policy_changed:
                    allow_text = False
                if enabled and config is None:
                    raise InvalidAgentOutputConfig("output_config is required when structured output is enabled")
                if allow_text and config is None and (definition_changed or policy_changed):
                    raise InvalidAgentOutputConfig("output_config is required when allow_text_output is true")
                updates = data.model_dump(exclude_unset=True, exclude={"output_config", "allow_text_output"})
                # Keep the legacy column for existing databases, but never use its old per-tool scope.
                updates["ui_tool_selection"] = {}
                for field, value in updates.items():
                    setattr(row, field, value)
                if user_id is not None:
                    row.updated_by = trusted_updater(user_id)
                row.updated_at = datetime.now(UTC)
                if definition_changed or policy_changed:
                    await save_output_config(db, agent_id, config, allow_text_output=allow_text)
                await db.flush()
                await db.refresh(row)
                # The scalar property in refresh can still use MySQL's initial
                # consistent-read view when only the parent changed. Hydrate
                # its read-only value from the current config we locked above;
                # this does not turn the property into an Agent write column.
                set_committed_value(row, "allow_text_output", allow_text)
                return row
        except IntegrityError as exc:
            if not is_unique_conflict(exc):
                raise
            raise ValueError(f"Agent write conflict: {agent_id}") from exc

    async def list_referencing_capability(self, capability: str) -> list[Agent]:
        async with self._read() as db:
            rows = await self._list_all(db)
            return [row for row in rows if capability in (row.capabilities or [])]

    async def list_referencing_skill(self, skill_code: str) -> list[Agent]:
        async with self._read() as db:
            rows = await self._list_all(db)
            return [row for row in rows if skill_code in (row.skills or [])]

    async def _deletion_impact(self, db, agent_id: str, user_id: str) -> DeletionImpact | None:
        if await lock_optional(db, Agent, Agent.id, agent_id) is None:
            return None
        if (await db.execute(select(ChannelAccount.id).where(
            ChannelAccount.agent_id == agent_id).limit(1).with_for_update())).first():
            raise ReferenceConflict("Agent 正被渠道配置使用，请先将对应渠道改绑到其他 Agent 或删除渠道；仅停用渠道不会解除关联")
        sessions = (await db.execute(select(ChatSession.session_id, ChatSession.user_id, ChatSession.created_at)
            .where(ChatSession.agent_id == agent_id).order_by(ChatSession.session_id).with_for_update())).all()
        return DeletionImpact.build("agent", agent_id, user_id, sessions=[tuple(row) for row in sessions])

    async def deletion_impact(self, agent_id: str, *, user_id: str) -> DeletionImpact | None:
        async with self._write() as db:
            return await self._deletion_impact(db, agent_id, user_id)

    async def delete(self, agent_id: str, *, user_id: str = "", confirmation_token: str | None = None) -> bool:
        async with self._write() as db:
            impact = await self._deletion_impact(db, agent_id, user_id)
            if impact is None:
                return False
            if (impact.counts["session_count"] or confirmation_token) and confirmation_token != impact.token:
                raise DeletionConfirmationRequired(impact)
            sessions = select(ChatSession.session_id).where(ChatSession.agent_id == agent_id)
            await db.execute(update(ChannelSessionBinding).where(ChannelSessionBinding.session_id.in_(sessions)).values(session_id=None))
            await db.execute(delete(ChatSessionEvent).where(ChatSessionEvent.session_id.in_(sessions)))
            await db.execute(delete(ChatSession).where(ChatSession.agent_id == agent_id))
            await db.execute(delete(AgentOutputConfig).where(AgentOutputConfig.agent_id == agent_id))
            result = await db.execute(delete(Agent).where(Agent.id == agent_id))
            await db.flush()
            return result.rowcount > 0

    @staticmethod
    async def _lock_mcp_servers(db: AsyncSession, capabilities: list[str], *, old_capabilities=()) -> None:
        from deep_agent.capabilities.mcp.builtin_config import is_shipped_builtin
        names = {code.removeprefix("mcp:") for code in [*old_capabilities, *capabilities] if code.startswith("mcp:")}
        for name in sorted(names):
            row = await lock_optional(db, McpServer, McpServer.name, name)
            if row is None and f"mcp:{name}" in capabilities and not is_shipped_builtin(name):
                raise ReferenceConflict(f"MCP not found: {name}")

    @staticmethod
    async def _lock_ui_packages(db: AsyncSession, capabilities: list[str]) -> None:
        # builtin UI definitions live in code, not in this table.
        for code in sorted({c for c in capabilities if c.startswith("ui:") and c != "ui:builtin"}):
            await lock_reference(db, UiToolPackage, UiToolPackage.code, code, "UI tool package")

    async def _get_by_id(self, db: AsyncSession, agent_id: str) -> Agent | None:
        result = await db.execute(select(Agent).where(Agent.id == agent_id))
        return result.scalar_one_or_none()

    async def _get_by_name(self, db: AsyncSession, name: str) -> Agent | None:
        result = await db.execute(select(Agent).where(Agent.name == name))
        return result.scalar_one_or_none()

    async def _list_all(self, db: AsyncSession) -> list[Agent]:
        result = await db.execute(select(Agent).order_by(Agent.name))
        return list(result.scalars().all())
