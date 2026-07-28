"""model catalog and LLM runtime metadata

Revision ID: 022
Revises: 021
Create Date: 2026-07-28

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import (
    has_column,
    has_table,
    is_sqlite,
    now_default,
    text_json_type,
)


revision: str = "022"
down_revision: Union[str, Sequence[str], None] = "021"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_llm_model_columns(bind) -> None:
    columns = [
        sa.Column("context_window_tokens", sa.BigInteger(), nullable=True),
        sa.Column("max_output_tokens", sa.BigInteger(), nullable=True),
        sa.Column("tags", text_json_type(bind), nullable=True),
    ]
    missing = [column for column in columns if not has_column(bind, "llm_models", column.name)]
    if not missing:
        return
    if is_sqlite(bind):
        with op.batch_alter_table("llm_models") as batch_op:
            for column in missing:
                batch_op.add_column(column)
        return
    for column in missing:
        op.add_column("llm_models", column)


def _drop_llm_capabilities_source(bind) -> None:
    if not has_column(bind, "llm_models", "capabilities_source"):
        return
    if is_sqlite(bind):
        with op.batch_alter_table("llm_models") as batch_op:
            batch_op.drop_column("capabilities_source")
        return
    op.drop_column("llm_models", "capabilities_source")


def _create_model_catalog(bind) -> None:
    if has_table(bind, "model_catalog"):
        return
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
        sa.Column("input_modalities", text_json_type(bind), nullable=False),
        sa.Column("output_modalities", text_json_type(bind), nullable=False),
        sa.Column("capabilities", text_json_type(bind), nullable=False),
        sa.Column("status", sa.String(32), nullable=True),
        sa.Column("lab_metadata", text_json_type(bind), nullable=False),
        sa.Column("model_metadata", text_json_type(bind), nullable=False),
        sa.Column("provider_metadata", text_json_type(bind), nullable=False),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default(bind),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=now_default(bind),
        ),
        sa.PrimaryKeyConstraint("provider_id", "provider_model_id"),
    )
    op.create_index("ix_model_catalog_lab_id", "model_catalog", ["lab_id"])
    op.create_index(
        "ix_model_catalog_canonical_model_id",
        "model_catalog",
        ["canonical_model_id"],
    )
    op.create_index("ix_model_catalog_provider_id", "model_catalog", ["provider_id"])
    op.create_index("ix_model_catalog_model_label", "model_catalog", ["model_label"])


def upgrade() -> None:
    bind = op.get_bind()
    _add_llm_model_columns(bind)
    _drop_llm_capabilities_source(bind)
    _create_model_catalog(bind)


def downgrade() -> None:
    bind = op.get_bind()
    if has_table(bind, "model_catalog"):
        op.drop_table("model_catalog")

    column_names = ("tags", "max_output_tokens", "context_window_tokens")
    existing = [name for name in column_names if has_column(bind, "llm_models", name)]
    if existing:
        if is_sqlite(bind):
            with op.batch_alter_table("llm_models") as batch_op:
                for name in existing:
                    batch_op.drop_column(name)
        else:
            for name in existing:
                op.drop_column("llm_models", name)

    if not has_column(bind, "llm_models", "capabilities_source"):
        column = sa.Column("capabilities_source", sa.String(32), nullable=True)
        if is_sqlite(bind):
            with op.batch_alter_table("llm_models") as batch_op:
                batch_op.add_column(column)
        else:
            op.add_column("llm_models", column)
