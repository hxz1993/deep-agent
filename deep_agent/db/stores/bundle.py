from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.conversation import SessionStore, SqlConversationCoordinator
from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.agent import AgentStoreBase, create_agent_store
from deep_agent.db.stores.auxiliary_model import AuxiliaryModelStoreBase, create_auxiliary_model_store
from deep_agent.db.stores.chat_session import ChatSessionStoreBase, create_chat_session_store
from deep_agent.db.stores.context_checkpoint import (
    ContextCheckpointStoreBase,
    create_context_checkpoint_store,
)
from deep_agent.db.stores.llm_model import LlmModelStoreBase, create_llm_model_store
from deep_agent.db.stores.mcp import McpServerStoreBase, create_mcp_store
from deep_agent.db.stores.message import MessageStoreBase, create_message_store
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
    message: MessageStoreBase
    context_checkpoint: ContextCheckpointStoreBase
    coordinator: SessionStore
    agent: AgentStoreBase
    llm_model: LlmModelStoreBase
    llm_configuration: LlmConfigurationService
    model_catalog: ModelCatalogStoreBase
    auxiliary_model: AuxiliaryModelStoreBase
    mcp: McpServerStoreBase
    ui_tool: UiToolStoreBase
    ui_tool_package: UiToolPackageStoreBase
    skill_package: SkillPackageStoreBase


def create_data_stores(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> DataStores:
    message = create_message_store(dialect, session_factory)
    chat_session = create_chat_session_store(dialect, session_factory, message_store=message)
    context_checkpoint = create_context_checkpoint_store(dialect, session_factory)

    return DataStores(
        chat_session=chat_session,
        message=message,
        context_checkpoint=context_checkpoint,
        coordinator=SqlConversationCoordinator(
            session_factory,
            chat_session=chat_session,
            message=message,
            context_checkpoint=context_checkpoint,
        ),
        agent=create_agent_store(dialect, session_factory),
        llm_model=create_llm_model_store(dialect, session_factory),
        llm_configuration=LlmConfigurationService(session_factory),
        model_catalog=create_model_catalog_store(dialect, session_factory),
        auxiliary_model=create_auxiliary_model_store(dialect, session_factory),
        mcp=create_mcp_store(dialect, session_factory),
        ui_tool=create_ui_tool_store(dialect, session_factory),
        ui_tool_package=create_ui_tool_package_store(dialect, session_factory),
        skill_package=create_skill_package_store(dialect, session_factory),
    )
