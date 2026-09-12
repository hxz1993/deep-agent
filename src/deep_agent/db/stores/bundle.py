from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.conversation import SessionStore, SqlConversationCoordinator
from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.agent import AgentStoreBase, create_agent_store
from deep_agent.db.stores.chat_session import ChatSessionStoreBase, create_chat_session_store
from deep_agent.db.stores.channel import ChannelStore
from deep_agent.db.stores.event import SessionEventStore
from deep_agent.db.stores.global_setting import GlobalSettingStore
from deep_agent.db.stores.integration_settings import IntegrationSettingsStore
from deep_agent.db.stores.llm_model import LlmModelStoreBase, create_llm_model_store
from deep_agent.db.stores.mcp import McpServerStoreBase, create_mcp_store
from deep_agent.db.stores.model_catalog import ModelCatalogStoreBase, create_model_catalog_store
from deep_agent.db.stores.skill_package import SkillPackageStoreBase, create_skill_package_store
from deep_agent.db.stores.ui_tool import UiToolStoreBase, create_ui_tool_store
from deep_agent.db.stores.ui_tool_package import (
    UiToolPackageStoreBase,
    create_ui_tool_package_store,
)
from deep_agent.services.llm_configuration import LlmConfigurationService

__all__ = ["DataStores", "create_data_stores"]


@dataclass
class DataStores:
    chat_session: ChatSessionStoreBase
    events: SessionEventStore
    coordinator: SessionStore
    agent: AgentStoreBase
    llm_model: LlmModelStoreBase
    llm_configuration: LlmConfigurationService
    model_catalog: ModelCatalogStoreBase
    mcp: McpServerStoreBase
    ui_tool: UiToolStoreBase
    ui_tool_package: UiToolPackageStoreBase
    skill_package: SkillPackageStoreBase
    # Optional keeps manually assembled test/integration bundles compatible;
    # the production factory below always supplies the store.
    global_setting: GlobalSettingStore | None = None
    channel: ChannelStore | None = None
    integration_settings: IntegrationSettingsStore | None = None


def create_data_stores(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> DataStores:
    events = SessionEventStore(session_factory)
    chat_session = create_chat_session_store(dialect, session_factory)

    return DataStores(
        chat_session=chat_session,
        events=events,
        coordinator=SqlConversationCoordinator(
            session_factory,
            chat_session=chat_session,
            events=events,
        ),
        agent=create_agent_store(dialect, session_factory),
        llm_model=create_llm_model_store(dialect, session_factory),
        llm_configuration=LlmConfigurationService(session_factory),
        model_catalog=create_model_catalog_store(dialect, session_factory),
        mcp=create_mcp_store(dialect, session_factory),
        ui_tool=create_ui_tool_store(dialect, session_factory),
        ui_tool_package=create_ui_tool_package_store(dialect, session_factory),
        skill_package=create_skill_package_store(dialect, session_factory),
        channel=ChannelStore(session_factory),
        global_setting=GlobalSettingStore(session_factory),
        integration_settings=IntegrationSettingsStore(session_factory),
    )
