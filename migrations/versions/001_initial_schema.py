"""DeepAgent v0.6.6 production baseline schema.

Revision ID: 001
Revises:
Create Date: 2026-09-01

This baseline is the complete schema previously represented by revisions
001 through 012. Existing revision-012 databases must be physically validated
and re-stamped with ``maintenance.rebaseline_database``; this migration must
never be replayed over an existing database.
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import (
    boolean_default,
    key_type,
    now_default,
    text_json_type,
)


revision: str = "001"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AGENT_ID_LENGTH = 36
PROVIDER_ID_LENGTH = 32
DEFAULT_PROVIDER_ID = "00000000000000000000000000000001"
DEFAULT_MODEL_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_AGENT_ID = "00000000-0000-0000-0000-000000000001"
DEFAULT_AGENT_PROMPT = (
    "You are deep-agent, a helpful general-purpose assistant. "
    "Use the available tools when they help you answer accurately. "
    "Be concise and explain your reasoning when it is useful."
)


def _bind():
    return op.get_bind()


def _key_type():
    return key_type(_bind())


def _json_type():
    return text_json_type(_bind())


def _now_default():
    return now_default(_bind())


def _true_default():
    return boolean_default(_bind(), value=True)


def _false_default():
    return boolean_default(_bind(), value=False)


def _seed_initial_configuration() -> None:
    bind = _bind()
    providers = sa.table(
        "llm_providers",
        sa.column("id", sa.String(PROVIDER_ID_LENGTH)),
        sa.column("name", _key_type()),
        sa.column("provider_type", sa.String(64)),
        sa.column("use_default_base_url", sa.Boolean()),
        sa.column("base_url", sa.Text()),
        sa.column("api_key", sa.Text()),
    )
    models = sa.table(
        "llm_models",
        sa.column("id", sa.String(AGENT_ID_LENGTH)),
        sa.column("provider_id", sa.String(PROVIDER_ID_LENGTH)),
        sa.column("name", _key_type()),
        sa.column("model", sa.Text()),
        sa.column("enabled", sa.Boolean()),
    )
    agents = sa.table(
        "agents",
        sa.column("id", sa.String(AGENT_ID_LENGTH)),
        sa.column("name", _key_type()),
        sa.column("model_id", sa.String(AGENT_ID_LENGTH)),
        sa.column("capabilities", _json_type()),
        sa.column("skills", _json_type()),
        sa.column("prompt", sa.Text()),
    )
    mcp_servers = sa.table(
        "mcp_servers",
        sa.column("name", _key_type()),
        sa.column("transport", sa.Text()),
        sa.column("config", _json_type()),
        sa.column("inspection", _json_type()),
        sa.column("description", sa.Text()),
    )
    global_settings = sa.table(
        "global_settings",
        sa.column("key", _key_type()),
        sa.column("value", _json_type()),
        sa.column("version", sa.SmallInteger()),
    )

    bind.execute(
        providers.insert().values(
            id=DEFAULT_PROVIDER_ID,
            name="OpenAI",
            provider_type="openai",
            use_default_base_url=True,
            base_url="https://api.openai.com/v1",
            api_key=None,
        )
    )
    bind.execute(
        models.insert().values(
            id=DEFAULT_MODEL_ID,
            provider_id=DEFAULT_PROVIDER_ID,
            name="openai:gpt-4o",
            model="openai:gpt-4o",
            enabled=True,
        )
    )
    bind.execute(
        agents.insert().values(
            id=DEFAULT_AGENT_ID,
            name="default",
            model_id=DEFAULT_MODEL_ID,
            capabilities="[]",
            skills="[]",
            prompt=DEFAULT_AGENT_PROMPT,
        )
    )
    bind.execute(
        mcp_servers.insert().values(
            name="deepcube",
            transport="builtin",
            config=json.dumps(
                {
                    "env": {
                        "DEEPCUBE_MCP_TRANSPORT": "stdio",
                        "DEEPCUBE_MCP_ENV_URL": "https://test.deepfos.com/deepfos-server/",
                        "DEEPCUBE_MCP_HEADERS": "{}",
                        "DEEPCUBE_MCP_DIM_INIT_MAX_CONCURRENCY": "4",
                    }
                },
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            inspection='{"status":"not_applicable"}',
            description=(
                "DeepCube SDK assistant: knowledge tools + live "
                "cube/dimension/member metadata."
            ),
        )
    )
    bind.execute(
        global_settings.insert().values(
            key="web_search",
            value='{"mode":"native"}',
            version=1,
        )
    )


def upgrade() -> None:
    op.create_table(
        "global_settings",
        sa.Column("key", _key_type(), nullable=False),
        sa.Column("value", _json_type(), nullable=False),
        sa.Column("version", sa.SmallInteger(), server_default="1", nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )

    op.create_table(
        "llm_providers",
        sa.Column("id", sa.String(PROVIDER_ID_LENGTH), nullable=False),
        sa.Column("name", _key_type(), nullable=False),
        sa.Column("provider_type", sa.String(64), nullable=False),
        sa.Column("use_default_base_url", sa.Boolean(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_llm_providers_name"),
    )

    op.create_table(
        "mcp_servers",
        sa.Column("name", _key_type(), nullable=False),
        sa.Column("transport", sa.Text(), nullable=False),
        sa.Column("config", _json_type(), nullable=False),
        sa.Column("inspection", _json_type(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
    )

    op.create_table(
        "model_catalog",
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("provider_model_id", _key_type(), nullable=False),
        sa.Column("provider_label", _key_type(), nullable=False),
        sa.Column("provider_npm", _key_type(), nullable=True),
        sa.Column("provider_api_url", sa.Text(), nullable=True),
        sa.Column("provider_doc_url", sa.Text(), nullable=True),
        sa.Column("lab_id", sa.String(64), nullable=False),
        sa.Column("lab_label", _key_type(), nullable=False),
        sa.Column("lab_description", sa.Text(), nullable=True),
        sa.Column("canonical_model_id", _key_type(), nullable=False),
        sa.Column("model_label", _key_type(), nullable=False),
        sa.Column("model_description", sa.Text(), nullable=True),
        sa.Column("family", sa.String(128), nullable=True),
        sa.Column("release_date", sa.String(32), nullable=True),
        sa.Column("canonical_context_window_tokens", sa.BigInteger(), nullable=False),
        sa.Column("canonical_max_output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("context_window_tokens", sa.BigInteger(), nullable=False),
        sa.Column("max_output_tokens", sa.BigInteger(), nullable=False),
        sa.Column("input_modalities", _json_type(), nullable=False),
        sa.Column("output_modalities", _json_type(), nullable=False),
        sa.Column("capabilities", _json_type(), nullable=False),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("lab_metadata", _json_type(), nullable=False),
        sa.Column("model_metadata", _json_type(), nullable=False),
        sa.Column("provider_metadata", _json_type(), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("provider_id", "provider_model_id"),
    )
    op.create_index("ix_model_catalog_lab_id", "model_catalog", ["lab_id"])
    op.create_index("ix_model_catalog_canonical_model_id", "model_catalog", ["canonical_model_id"])
    op.create_index("ix_model_catalog_provider_id", "model_catalog", ["provider_id"])
    op.create_index("ix_model_catalog_model_label", "model_catalog", ["model_label"])

    op.create_table(
        "skill_packages",
        sa.Column("code", _key_type(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("version", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("platform_file_id", _key_type(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("manifest", _json_type(), nullable=False),
        sa.Column("resource_index", _json_type(), nullable=False),
        sa.Column("contains_scripts", sa.Boolean(), server_default=_false_default(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )

    op.create_table(
        "ui_tool_packages",
        sa.Column("code", _key_type(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("code"),
    )

    op.create_table(
        "llm_models",
        sa.Column("id", sa.String(AGENT_ID_LENGTH), nullable=False),
        sa.Column("provider_id", sa.String(PROVIDER_ID_LENGTH), nullable=False),
        sa.Column("name", _key_type(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("input_modalities", _json_type(), nullable=True),
        sa.Column("context_window_tokens", sa.BigInteger(), nullable=True),
        sa.Column("max_output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("tags", _json_type(), nullable=True),
        sa.Column("enabled", sa.Boolean(), server_default=_true_default(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("provider_id", "name", name="uq_llm_models_provider_name"),
        sa.ForeignKeyConstraint(
            ["provider_id"],
            ["llm_providers.id"],
            name="fk_llm_models_provider_id_llm_providers",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_llm_models_provider_id", "llm_models", ["provider_id"])

    op.create_table(
        "ui_tool_definitions",
        sa.Column("name", _key_type(), nullable=False),
        sa.Column("package_code", _key_type(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("parameters_json_schema", _json_type(), nullable=False),
        sa.Column("widget", sa.String(64), nullable=True),
        sa.Column("result_schema", _json_type(), nullable=True),
        sa.Column("metadata", _json_type(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("name"),
        sa.ForeignKeyConstraint(
            ["package_code"],
            ["ui_tool_packages.code"],
            name="ui_tool_definitions_package_code_fkey",
            ondelete="RESTRICT",
        ),
    )
    op.create_index("ix_ui_tool_definitions_package_code", "ui_tool_definitions", ["package_code"])

    op.create_table(
        "agents",
        sa.Column("id", sa.String(AGENT_ID_LENGTH), nullable=False),
        sa.Column("name", _key_type(), nullable=False),
        sa.Column("model_id", sa.String(AGENT_ID_LENGTH), nullable=False),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("capabilities", _json_type(), nullable=False),
        sa.Column("skills", _json_type(), nullable=False),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_agents_name"),
        sa.ForeignKeyConstraint(
            ["model_id"],
            ["llm_models.id"],
            name="agents_model_id_fkey",
            ondelete="RESTRICT",
        ),
    )

    op.create_table(
        "chat_sessions",
        sa.Column("session_id", _key_type(), nullable=False),
        sa.Column("agent_id", sa.String(AGENT_ID_LENGTH), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=False),
        sa.Column("app_id", _key_type(), nullable=True),
        sa.Column("space_id", _key_type(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("next_sequence", sa.BigInteger(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("session_id", name="chat_sessions_pkey"),
        sa.ForeignKeyConstraint(
            ["agent_id"],
            ["agents.id"],
            name="fk_chat_sessions_agent_id_agents",
            ondelete="RESTRICT",
        ),
    )
    op.create_index(
        "ix_chat_sessions_agent_user_updated",
        "chat_sessions",
        ["agent_id", "user_id", "updated_at"],
    )

    op.create_table(
        "chat_session_events",
        sa.Column("event_id", sa.String(32), nullable=False),
        sa.Column("session_id", _key_type(), nullable=False),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("turn_id", sa.String(128), nullable=True),
        sa.Column("run_id", sa.String(128), nullable=True),
        sa.Column("search_text", sa.Text(), nullable=True),
        sa.Column("payload", _json_type(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
        sa.PrimaryKeyConstraint("event_id"),
        sa.UniqueConstraint("session_id", "sequence", name="uq_chat_session_events_session_sequence"),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["chat_sessions.session_id"],
            name="fk_chat_session_events_session_id_sessions",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_chat_session_events_session_turn_sequence",
        "chat_session_events",
        ["session_id", "turn_id", "sequence"],
    )
    op.create_index(
        "ix_chat_session_events_session_run_sequence",
        "chat_session_events",
        ["session_id", "run_id", "sequence"],
    )
    op.create_index(
        "ix_chat_session_events_session_type_status_sequence",
        "chat_session_events",
        ["session_id", "event_type", "status", "sequence"],
    )

    _seed_initial_configuration()


def downgrade() -> None:
    op.drop_table("chat_session_events")
    op.drop_index("ix_chat_sessions_agent_user_updated", table_name="chat_sessions")
    op.drop_table("chat_sessions")
    op.drop_table("agents")
    op.drop_index("ix_ui_tool_definitions_package_code", table_name="ui_tool_definitions")
    op.drop_table("ui_tool_definitions")
    op.drop_index("ix_llm_models_provider_id", table_name="llm_models")
    op.drop_table("llm_models")
    op.drop_table("llm_providers")
    op.drop_table("ui_tool_packages")
    op.drop_table("skill_packages")
    op.drop_table("model_catalog")
    op.drop_table("mcp_servers")
    op.drop_table("global_settings")
