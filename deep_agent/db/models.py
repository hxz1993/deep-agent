from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from deep_agent.db.ids import AGENT_ID_LENGTH, new_agent_id
from deep_agent.db.types import JSONType


class Base(DeclarativeBase):
    pass


KeyText = String(191)


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("name", name="uq_agents_name"),)

    id: Mapped[str] = mapped_column(String(AGENT_ID_LENGTH), primary_key=True, default=new_agent_id)
    name: Mapped[str] = mapped_column(KeyText, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    capabilities: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    max_requests: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class McpServer(Base):
    __tablename__ = "mcp_servers"

    name: Mapped[str] = mapped_column(KeyText, primary_key=True)
    transport: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONType, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"

    session_key: Mapped[str] = mapped_column(KeyText, primary_key=True)
    agent_id: Mapped[str | None] = mapped_column(
        String(AGENT_ID_LENGTH),
        ForeignKey("agents.id", ondelete="SET NULL"),
        nullable=True,
    )
    user_id: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
        order_by="ChatMessage.sequence",
    )


class UiToolDefinition(Base):
    __tablename__ = "ui_tool_definitions"

    name: Mapped[str] = mapped_column(KeyText, primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    parameters_json_schema: Mapped[dict] = mapped_column(JSONType, nullable=False)
    widget: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_schema: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    mcp_server_name: Mapped[str | None] = mapped_column(
        KeyText,
        ForeignKey("mcp_servers.name", ondelete="SET NULL"),
        nullable=True,
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    override: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    agent_ids: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    discovered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (UniqueConstraint("session_key", "sequence", name="uq_chat_messages_session_sequence"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    session_key: Mapped[str] = mapped_column(
        KeyText,
        ForeignKey("chat_sessions.session_key", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)

    session: Mapped[ChatSession] = relationship(back_populates="messages")
