from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from deep_agent.db.dialect import DbDialect
from deep_agent.db.stores.agent import AgentStoreBase, create_agent_store
from deep_agent.db.stores.chat_session import ChatSessionStoreBase, create_chat_session_store
from deep_agent.db.stores.conversation.memory import InMemoryConversationStore
from deep_agent.db.stores.conversation.protocol import SessionStore
from deep_agent.db.stores.conversation.sql import SqlConversationStore
from deep_agent.db.stores.mcp import McpServerStoreBase, create_mcp_store
from deep_agent.db.stores.message import MessageStoreBase, create_message_store
from deep_agent.db.stores.session_query import SessionQueryStoreBase, create_session_query_store
from deep_agent.db.stores.ui_tool import UiToolStoreBase, create_ui_tool_store

__all__ = ["DataStores", "create_data_stores"]


@dataclass
class DataStores:
    conversation: SessionStore
    agent: AgentStoreBase | None
    mcp: McpServerStoreBase | None
    ui_tool: UiToolStoreBase | None


def create_data_stores(
    dialect: DbDialect,
    session_factory: async_sessionmaker[AsyncSession] | None,
) -> DataStores:
    if session_factory is None:
        return DataStores(
            conversation=InMemoryConversationStore(),
            agent=None,
            mcp=None,
            ui_tool=None,
        )

    message = create_message_store(dialect, session_factory)
    chat_session = create_chat_session_store(dialect, session_factory)
    session_query = create_session_query_store(dialect, session_factory, message)

    return DataStores(
        conversation=SqlConversationStore(
            session_factory,
            chat_session=chat_session,
            message=message,
            session_query=session_query,
        ),
        agent=create_agent_store(dialect, session_factory),
        mcp=create_mcp_store(dialect, session_factory),
        ui_tool=create_ui_tool_store(dialect, session_factory),
    )
