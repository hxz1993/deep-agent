from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.conversation import SessionStore, SqlConversationCoordinator
from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.agent import AgentStoreBase, create_agent_store
from deep_agent.db.stores.auxiliary_model import AuxiliaryModelStoreBase, create_auxiliary_model_store
from deep_agent.db.stores.chat_session import ChatSessionStoreBase, create_chat_session_store
from deep_agent.db.stores.llm_model import LlmModelStoreBase, create_llm_model_store
from deep_agent.db.stores.mcp import McpServerStoreBase, create_mcp_store
from deep_agent.db.stores.message import MessageStoreBase, create_message_store
from deep_agent.db.stores.ui_tool import UiToolStoreBase, create_ui_tool_store

__all__ = ["DataStores", "create_data_stores"]


@dataclass
class DataStores:
    chat_session: ChatSessionStoreBase
    message: MessageStoreBase
    coordinator: SessionStore
    agent: AgentStoreBase
    llm_model: LlmModelStoreBase
    auxiliary_model: AuxiliaryModelStoreBase
    mcp: McpServerStoreBase
    ui_tool: UiToolStoreBase


def create_data_stores(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession],
) -> DataStores:
    message = create_message_store(dialect, session_factory)
    chat_session = create_chat_session_store(dialect, session_factory, message_store=message)

    return DataStores(
        chat_session=chat_session,
        message=message,
        coordinator=SqlConversationCoordinator(
            session_factory,
            chat_session=chat_session,
            message=message,
        ),
        agent=create_agent_store(dialect, session_factory),
        llm_model=create_llm_model_store(dialect, session_factory),
        auxiliary_model=create_auxiliary_model_store(dialect, session_factory),
        mcp=create_mcp_store(dialect, session_factory),
        ui_tool=create_ui_tool_store(dialect, session_factory),
    )
