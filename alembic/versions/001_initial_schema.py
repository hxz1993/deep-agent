"""DeepAgent v0.3.0 baseline schema.

Revision ID: 001
Revises:
Create Date: 2026-08-03

This is the only supported database baseline.  Repositories released before
v0.3.0 used revisions 001 through 023; existing databases must be validated
against this schema and re-stamped instead of replaying the removed history.
"""

from __future__ import annotations

import json
from typing import Sequence, Union

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


def _seed_initial_configuration() -> None:
    bind = _bind()
    llm_models = sa.table(
        "llm_models",
        sa.column("id", sa.String(AGENT_ID_LENGTH)),
        sa.column("name", _key_type()),
        sa.column("model", sa.Text()),
    )
    agents = sa.table(
        "agents",
        sa.column("id", sa.String(AGENT_ID_LENGTH)),
        sa.column("name", _key_type()),
        sa.column("model_id", sa.String(AGENT_ID_LENGTH)),
        sa.column("capabilities", _json_type()),
        sa.column("prompt", sa.Text()),
    )
    mcp_servers = sa.table(
        "mcp_servers",
        sa.column("name", _key_type()),
        sa.column("transport", sa.Text()),
        sa.column("config", _json_type()),
        sa.column("inspection", _json_type()),
        sa.column("description", sa.Text()),
        sa.column("enabled", sa.Boolean()),
    )
    auxiliary_models = sa.table(
        "auxiliary_model_configs",
        sa.column("use_case", sa.String(64)),
        sa.column("llm_model_id", sa.String(AGENT_ID_LENGTH)),
    )

    bind.execute(
        llm_models.insert().values(
            id=DEFAULT_MODEL_ID,
            name="openai:gpt-4o",
            model="openai:gpt-4o",
        )
    )
    bind.execute(
        agents.insert().values(
            id=DEFAULT_AGENT_ID,
            name="default",
            model_id=DEFAULT_MODEL_ID,
            capabilities=None,
            prompt=DEFAULT_AGENT_PROMPT,
        )
    )
    bind.execute(
        mcp_servers.insert(),
        [
            {
                "name": "deepcube",
                "transport": "builtin",
                "config": json.dumps(
                    {
                        "env": {
                            "DEEPCUBE_MCP_TRANSPORT": "stdio",
                            "DEEPCUBE_MCP_ENV_URL": (
                                "https://test.deepfos.com/deepfos-server/"
                            ),
                            "DEEPCUBE_MCP_HEADERS": "{}",
                            "DEEPCUBE_MCP_DIM_INIT_MAX_CONCURRENCY": "4",
                        }
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
                "inspection": '{"status":"not_applicable"}',
                "description": (
                    "DeepCube SDK assistant: knowledge tools + live "
                    "cube/dimension/member metadata."
                ),
                "enabled": True,
            },
            {
                "name": "doc_search",
                "transport": "builtin",
                "config": "{}",
                "inspection": '{"status":"not_applicable"}',
                "description": "Builtin MCP server 'doc_search'.",
                "enabled": True,
            },
            {
                "name": "text_tools",
                "transport": "builtin",
                "config": "{}",
                "inspection": '{"status":"not_applicable"}',
                "description": "Builtin MCP server 'text_tools'.",
                "enabled": True,
            },
        ],
    )
    bind.execute(
        auxiliary_models.insert().values(use_case="title", llm_model_id=None)
    )


def upgrade() -> None:
    op.create_table(
        "llm_models",
        sa.Column("id", sa.String(AGENT_ID_LENGTH), nullable=False),
        sa.Column("name", _key_type(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=True),
        sa.Column("api_key", sa.Text(), nullable=True),
        sa.Column("input_modalities", _json_type(), nullable=True),
        sa.Column("context_window_tokens", sa.BigInteger(), nullable=True),
        sa.Column("max_output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("tags", _json_type(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_llm_models_name"),
    )

    op.create_table(
        "model_catalog",
        sa.Column("provider_id", sa.String(64), nullable=False),
        sa.Column("provider_model_id", sa.String(191), nullable=False),
        sa.Column("provider_label", sa.String(191), nullable=False),
        sa.Column("provider_npm", sa.String(191), nullable=True),
        sa.Column("provider_api_url", sa.Text(), nullable=True),
        sa.Column("provider_doc_url", sa.Text(), nullable=True),
        sa.Column("lab_id", sa.String(64), nullable=False),
        sa.Column("lab_label", sa.String(191), nullable=False),
        sa.Column("lab_description", sa.Text(), nullable=True),
        sa.Column("canonical_model_id", sa.String(191), nullable=False),
        sa.Column("model_label", sa.String(191), nullable=False),
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
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("provider_id", "provider_model_id"),
    )
    op.create_index("ix_model_catalog_lab_id", "model_catalog", ["lab_id"])
    op.create_index(
        "ix_model_catalog_canonical_model_id",
        "model_catalog",
        ["canonical_model_id"],
    )
    op.create_index(
        "ix_model_catalog_provider_id",
        "model_catalog",
        ["provider_id"],
    )
    op.create_index(
        "ix_model_catalog_model_label",
        "model_catalog",
        ["model_label"],
    )

    op.create_table(
        "agents",
        sa.Column("id", sa.String(AGENT_ID_LENGTH), nullable=False),
        sa.Column("name", _key_type(), nullable=False),
        sa.Column(
            "model_id",
            sa.String(AGENT_ID_LENGTH),
            nullable=False,
        ),
        sa.Column("temperature", sa.Float(), nullable=True),
        sa.Column("capabilities", _json_type(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
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
        "mcp_servers",
        sa.Column("name", _key_type(), nullable=False),
        sa.Column("transport", sa.Text(), nullable=False),
        sa.Column("config", _json_type(), nullable=False),
        sa.Column("inspection", _json_type(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=_true_default(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
    )

    op.create_table(
        "chat_sessions",
        sa.Column(
            "agent_id",
            sa.String(AGENT_ID_LENGTH),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("session_id", _key_type(), nullable=False),
        sa.Column("user_id", sa.String(128), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("agent_id", "session_id", name="chat_sessions_pkey"),
    )
    op.create_index(
        "ix_chat_sessions_session_id",
        "chat_sessions",
        ["session_id"],
    )
    op.create_index(
        "ix_chat_sessions_updated_at",
        "chat_sessions",
        ["updated_at"],
    )
    op.create_index(
        "ix_chat_sessions_agent_id_updated_at",
        "chat_sessions",
        ["agent_id", "updated_at"],
    )
    op.create_index(
        "ix_chat_sessions_user_id_agent_id_updated_at",
        "chat_sessions",
        ["user_id", "agent_id", "updated_at"],
    )

    op.create_table(
        "ui_tool_definitions",
        sa.Column("name", _key_type(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("parameters_json_schema", _json_type(), nullable=False),
        sa.Column("widget", sa.String(64), nullable=True),
        sa.Column("result_schema", _json_type(), nullable=True),
        sa.Column("metadata", _json_type(), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=_true_default(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("name"),
    )
    op.create_index(
        "ix_ui_tool_definitions_source_enabled",
        "ui_tool_definitions",
        ["source", "enabled"],
    )

    op.create_table(
        "auxiliary_model_configs",
        sa.Column("use_case", sa.String(64), nullable=False),
        sa.Column("llm_model_id", sa.String(AGENT_ID_LENGTH), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("use_case"),
        sa.ForeignKeyConstraint(
            ["llm_model_id"],
            ["llm_models.id"],
            name="auxiliary_model_configs_llm_model_id_fkey",
            ondelete="SET NULL",
        ),
    )

    op.create_table(
        "chat_messages",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column(
            "agent_id",
            sa.String(AGENT_ID_LENGTH),
            server_default=sa.text("''"),
            nullable=False,
        ),
        sa.Column("session_id", _key_type(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("payload", _json_type(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "agent_id",
            "session_id",
            "sequence",
            name="uq_chat_messages_session_sequence",
        ),
    )

    op.create_table(
        "chat_context_checkpoints",
        sa.Column("agent_id", sa.String(AGENT_ID_LENGTH), nullable=False),
        sa.Column("session_id", _key_type(), nullable=False),
        sa.Column("source_last_sequence", sa.Integer(), nullable=False),
        sa.Column("source_history_hash", sa.String(64), nullable=False),
        sa.Column("payload", _json_type(), nullable=False),
        sa.Column("payload_schema_version", sa.Integer(), nullable=False),
        sa.Column("projection_fingerprint", sa.String(64), nullable=False),
        sa.Column("estimated_tokens", sa.BigInteger(), nullable=False),
        sa.Column("summary_model_id", sa.String(AGENT_ID_LENGTH), nullable=True),
        sa.Column(
            "row_version",
            sa.BigInteger(),
            server_default=sa.text("1"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=_now_default(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("agent_id", "session_id"),
    )

    _seed_initial_configuration()


def downgrade() -> None:
    op.drop_table("chat_context_checkpoints")
    op.drop_table("chat_messages")
    op.drop_table("auxiliary_model_configs")
    op.drop_table("ui_tool_definitions")
    op.drop_table("chat_sessions")
    op.drop_table("mcp_servers")
    op.drop_table("agents")
    op.drop_table("model_catalog")
    op.drop_table("llm_models")
