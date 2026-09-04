from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from deep_agent.db.ids import (
    AGENT_ID_LENGTH,
    PROVIDER_ID_LENGTH,
    new_agent_id,
    new_provider_id,
)
from deep_agent.db.types import JSONType, UtcTimestamp


class Base(DeclarativeBase):
    pass


KeyText = String(191)


class LlmModel(Base):
    __tablename__ = "llm_models"
    __table_args__ = (
        UniqueConstraint(
            "provider_id",
            "name",
            name="uq_llm_models_provider_name",
        ),
    )

    id: Mapped[str] = mapped_column(String(AGENT_ID_LENGTH), primary_key=True, default=new_agent_id)
    provider_id: Mapped[str] = mapped_column(
        String(PROVIDER_ID_LENGTH),
        ForeignKey("llm_providers.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(KeyText, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    input_modalities: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    context_window_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    max_output_tokens: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSONType, nullable=True)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="true")
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class LlmProvider(Base):
    __tablename__ = "llm_providers"
    __table_args__ = (UniqueConstraint("name", name="uq_llm_providers_name"),)

    id: Mapped[str] = mapped_column(
        String(PROVIDER_ID_LENGTH),
        primary_key=True,
        default=new_provider_id,
    )
    name: Mapped[str] = mapped_column(KeyText, nullable=False)
    provider_type: Mapped[str] = mapped_column(String(64), nullable=False)
    use_default_base_url: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    base_url: Mapped[str] = mapped_column(Text, nullable=False)
    api_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ModelCatalog(Base):
    __tablename__ = "model_catalog"
    __table_args__ = (
        PrimaryKeyConstraint("provider_id", "provider_model_id"),
        Index("ix_model_catalog_lab_id", "lab_id"),
        Index("ix_model_catalog_canonical_model_id", "canonical_model_id"),
        Index("ix_model_catalog_provider_id", "provider_id"),
        Index("ix_model_catalog_model_label", "model_label"),
    )

    provider_id: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_model_id: Mapped[str] = mapped_column(KeyText, nullable=False)
    provider_label: Mapped[str] = mapped_column(KeyText, nullable=False)
    provider_npm: Mapped[str | None] = mapped_column(KeyText, nullable=True)
    provider_api_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    provider_doc_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    lab_id: Mapped[str] = mapped_column(String(64), nullable=False)
    lab_label: Mapped[str] = mapped_column(KeyText, nullable=False)
    lab_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    canonical_model_id: Mapped[str] = mapped_column(KeyText, nullable=False)
    model_label: Mapped[str] = mapped_column(KeyText, nullable=False)
    model_description: Mapped[str | None] = mapped_column(Text, nullable=True)
    family: Mapped[str | None] = mapped_column(String(128), nullable=True)
    release_date: Mapped[str | None] = mapped_column(String(32), nullable=True)

    canonical_context_window_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    canonical_max_output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    context_window_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    max_output_tokens: Mapped[int] = mapped_column(BigInteger, nullable=False)
    input_modalities: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    output_modalities: Mapped[list[str]] = mapped_column(JSONType, nullable=False)
    capabilities: Mapped[dict] = mapped_column(JSONType, nullable=False)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)

    lab_metadata: Mapped[dict] = mapped_column(JSONType, nullable=False)
    model_metadata: Mapped[dict] = mapped_column(JSONType, nullable=False)
    provider_metadata: Mapped[dict] = mapped_column(JSONType, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    refreshed_at: Mapped[datetime] = mapped_column(UtcTimestamp(), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(),
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
    capabilities: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    skills: Mapped[list[str]] = mapped_column(JSONType, nullable=False, default=list)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    llm_model: Mapped[LlmModel] = relationship("LlmModel", lazy="joined")


class GlobalSetting(Base):
    __tablename__ = "global_settings"

    key: Mapped[str] = mapped_column(KeyText, primary_key=True)
    value: Mapped[dict] = mapped_column(JSONType, nullable=False)
    version: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        server_default="1",
    )
    created_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class SkillPackage(Base):
    __tablename__ = "skill_packages"

    code: Mapped[str] = mapped_column(KeyText, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    platform_file_id: Mapped[str] = mapped_column(KeyText, nullable=False)
    file_name: Mapped[str] = mapped_column(Text, nullable=False)
    file_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONType, nullable=False)
    resource_index: Mapped[list[dict]] = mapped_column(JSONType, nullable=False)
    contains_scripts: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    created_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class McpServer(Base):
    __tablename__ = "mcp_servers"

    name: Mapped[str] = mapped_column(KeyText, primary_key=True)
    transport: Mapped[str] = mapped_column(Text, nullable=False)
    config: Mapped[dict] = mapped_column(JSONType, nullable=False)
    inspection: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    __table_args__ = (
        Index(
            "ix_chat_sessions_agent_user_updated",
            "agent_id",
            "user_id",
            "updated_at",
        ),
    )

    session_id: Mapped[str] = mapped_column(KeyText, primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(AGENT_ID_LENGTH),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    app_id: Mapped[str | None] = mapped_column(KeyText, nullable=True)
    space_id: Mapped[str | None] = mapped_column(KeyText, nullable=True)
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    next_sequence: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
        default=1,
        server_default="1",
    )
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UiToolPackage(Base):
    __tablename__ = "ui_tool_packages"

    code: Mapped[str] = mapped_column(KeyText, primary_key=True)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class UiToolDefinition(Base):
    __tablename__ = "ui_tool_definitions"
    __table_args__ = (Index("ix_ui_tool_definitions_package_code", "package_code"),)

    name: Mapped[str] = mapped_column(KeyText, primary_key=True)
    package_code: Mapped[str] = mapped_column(
        KeyText,
        ForeignKey("ui_tool_packages.code", ondelete="RESTRICT"),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    parameters_json_schema: Mapped[dict] = mapped_column(JSONType, nullable=False)
    widget: Mapped[str | None] = mapped_column(String(64), nullable=True)
    result_schema: Mapped[dict | None] = mapped_column(JSONType, nullable=True)
    metadata_: Mapped[dict | None] = mapped_column("metadata", JSONType, nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
    package: Mapped[UiToolPackage] = relationship("UiToolPackage", lazy="joined")


class ChatSessionEvent(Base):
    __tablename__ = "chat_session_events"
    __table_args__ = (
        UniqueConstraint(
            "session_id",
            "sequence",
            name="uq_chat_session_events_session_sequence",
        ),
        Index(
            "ix_chat_session_events_session_turn_sequence",
            "session_id",
            "turn_id",
            "sequence",
        ),
        Index(
            "ix_chat_session_events_session_run_sequence",
            "session_id",
            "run_id",
            "sequence",
        ),
        Index(
            "ix_chat_session_events_session_type_status_sequence",
            "session_id",
            "event_type",
            "status",
            "sequence",
        ),
    )

    event_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    session_id: Mapped[str] = mapped_column(
        KeyText,
        ForeignKey("chat_sessions.session_id", ondelete="CASCADE"),
        nullable=False,
    )
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    schema_version: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    turn_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    search_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload: Mapped[dict] = mapped_column(JSONType, nullable=False)
    created_at: Mapped[datetime] = mapped_column(UtcTimestamp(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class ChannelAccount(Base):
    __tablename__ = "channel_accounts"
    __table_args__ = (
        UniqueConstraint(
            "channel_type",
            "bot_id",
            name="uq_channel_accounts_type_bot",
        ),
        Index("ix_channel_accounts_enabled", "enabled"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    name: Mapped[str] = mapped_column(KeyText, nullable=False)
    channel_type: Mapped[str] = mapped_column(
        String(32), nullable=False, default="wecom", server_default="wecom"
    )
    transport: Mapped[str] = mapped_column(
        String(32), nullable=False, default="websocket", server_default="websocket"
    )
    bot_id: Mapped[str] = mapped_column(KeyText, nullable=False)
    secret: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str] = mapped_column(
        String(AGENT_ID_LENGTH),
        ForeignKey("agents.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )
    session_user_id: Mapped[str] = mapped_column(String(128), nullable=False)
    enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    settings: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChannelSessionBinding(Base):
    __tablename__ = "channel_session_bindings"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "peer_type",
            "peer_id",
            name="uq_channel_bindings_account_peer",
        ),
        Index("ix_channel_bindings_session_id", "session_id"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("channel_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    peer_type: Mapped[str] = mapped_column(String(16), nullable=False)
    peer_id: Mapped[str] = mapped_column(KeyText, nullable=False)
    peer_display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    session_id: Mapped[str | None] = mapped_column(
        KeyText,
        ForeignKey("chat_sessions.session_id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class ChannelMessageRun(Base):
    __tablename__ = "channel_message_runs"
    __table_args__ = (
        UniqueConstraint(
            "account_id",
            "external_message_id",
            name="uq_channel_runs_account_message",
        ),
        Index("ix_channel_runs_binding_created", "binding_id", "created_at"),
        Index("ix_channel_runs_processing_status", "processing_status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    account_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("channel_accounts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    binding_id: Mapped[str] = mapped_column(
        String(32),
        ForeignKey("channel_session_bindings.id", ondelete="CASCADE"),
        nullable=False,
    )
    external_message_id: Mapped[str] = mapped_column(KeyText, nullable=False)
    external_request_id: Mapped[str | None] = mapped_column(KeyText, nullable=True)
    sender_id: Mapped[str | None] = mapped_column(KeyText, nullable=True)
    session_id: Mapped[str | None] = mapped_column(KeyText, nullable=True)
    agent_run_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    stream_id: Mapped[str | None] = mapped_column(KeyText, nullable=True)
    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    processing_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="received", server_default="received"
    )
    delivery_status: Mapped[str] = mapped_column(
        String(32), nullable=False, default="pending", server_default="pending"
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default="1"
    )
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UtcTimestamp(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UtcTimestamp(), server_default=func.now(), nullable=False
    )
