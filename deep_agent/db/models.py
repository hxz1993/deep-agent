from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, PrimaryKeyConstraint, String, Text, UniqueConstraint, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from deep_agent.db.ids import AGENT_ID_LENGTH, new_agent_id
from deep_agent.db.session_scope import NO_AGENT_SCOPE
from deep_agent.db.types import JSONType


class Base(DeclarativeBase):
    pass


KeyText = String(191)


class LlmModel(Base):
    __tablename__ = "llm_models"
    __table_args__ = (UniqueConstraint("name", name="uq_llm_models_name"),)

    id: Mapped[str] = mapped_column(String(AGENT_ID_LENGTH), primary_key=True, default=new_agent_id)
    name: Mapped[str] = mapped_column(KeyText, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    base_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    input_modalities: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    capabilities_source: Mapped[str | None] = mapped_column(String(32), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Agent(Base):
    __tablename__ = "agents"
    __table_args__ = (UniqueConstraint("name", name="uq_agents_name"),)

    id: Mapped[str] = mapped_column(String(AGENT_ID_LENGTH), primary_key=True, default=new_agent_id)
    name: Mapped[str] = mapped_column(KeyText, nullable=False)
    model_id: Mapped[str] = mapped_column(
        String(AGENT_ID_LENGTH),
        ForeignKey("llm_models.id", ondelete="RESTRICT"),
        nullable=False,
    )
    temperature: Mapped[float | None] = mapped_column(Float, nullable=True)
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
    llm_model: Mapped[LlmModel] = relationship("LlmModel", lazy="joined")


class McpServer(Base):
    __tablename__ = "mcp_servers"

    name: Mapped[str] = mapped_column(KeyText, primary_key=True)
    transport: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONType, nullable=False)
    resource_capabilities: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
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
    __table_args__ = (PrimaryKeyConstraint("agent_id", "session_id"),)

    agent_id: Mapped[str] = mapped_column(
        String(AGENT_ID_LENGTH),
        primary_key=True,
        default=NO_AGENT_SCOPE,
    )
    session_id: Mapped[str] = mapped_column(KeyText, primary_key=True)
    user_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
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
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class AuxiliaryModelConfig(Base):
    __tablename__ = "auxiliary_model_configs"

    use_case: Mapped[str] = mapped_column(String(64), primary_key=True)
    llm_model_id: Mapped[str | None] = mapped_column(
        String(AGENT_ID_LENGTH),
        ForeignKey("llm_models.id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    llm_model: Mapped[LlmModel | None] = relationship("LlmModel", lazy="joined")


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    __table_args__ = (
        UniqueConstraint("agent_id", "session_id", "sequence", name="uq_chat_messages_session_sequence"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    agent_id: Mapped[str] = mapped_column(String(AGENT_ID_LENGTH), nullable=False, default=NO_AGENT_SCOPE)
    session_id: Mapped[str] = mapped_column(KeyText, nullable=False)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
