"""llm_models table and agents.model_id FK

Revision ID: 013
Revises: 012
Create Date: 2026-06-25

"""

from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

from deep_agent.db.migration_dialect import (
    constraint_exists,
    create_foreign_key,
    has_column,
    has_table,
    is_sqlite,
    key_type,
    now_default,
)

revision: str = "013"
down_revision: Union[str, Sequence[str], None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AGENT_ID_LENGTH = 36
FK_NAME = "agents_model_id_fkey"


def _bind():
    return op.get_bind()


def _key_type():
    return key_type(_bind())


def _now_default():
    return now_default(_bind())


def _has_column(table_name: str, column_name: str) -> bool:
    return has_column(_bind(), table_name, column_name)


def _has_table(table_name: str) -> bool:
    return has_table(_bind(), table_name)


def _constraint_exists(table_name: str, constraint_name: str) -> bool:
    return constraint_exists(_bind(), table_name, constraint_name)


def _column_nullable(table_name: str, column_name: str) -> bool:
    for col in inspect(_bind()).get_columns(table_name):
        if col["name"] == column_name:
            return bool(col.get("nullable", True))
    return True


def _unique_model_name(model: str, used: set[str]) -> str:
    base = (model or "default").strip()[:180] or "default"
    name = base
    counter = 2
    while name in used:
        suffix = f" ({counter})"
        name = f"{base[: max(1, 180 - len(suffix))]}{suffix}"
        counter += 1
    used.add(name)
    return name


def _load_combo_to_id(bind) -> dict[tuple[str | None, str | None, str | None], str]:
    rows = bind.execute(
        sa.text("SELECT id, model, base_url, api_key FROM llm_models")
    ).fetchall()
    return {(model, base_url, api_key): llm_id for llm_id, model, base_url, api_key in rows}


def _ensure_llm_models(bind) -> dict[tuple[str | None, str | None, str | None], str]:
    combo_to_id = _load_combo_to_id(bind)
    used_names = {name for (name,) in bind.execute(sa.text("SELECT name FROM llm_models")).fetchall()}
    if not _has_column("agents", "model"):
        return combo_to_id
    agent_combos = bind.execute(
        sa.text("SELECT DISTINCT model, base_url, api_key FROM agents ORDER BY model, base_url, api_key")
    ).fetchall()
    for model, base_url, api_key in agent_combos:
        key = (model, base_url, api_key)
        if key in combo_to_id:
            continue
        llm_id = str(uuid.uuid4())
        name = _unique_model_name(model or "default", used_names)
        bind.execute(
            sa.text(
                """
                INSERT INTO llm_models (id, name, model, base_url, api_key)
                VALUES (:id, :name, :model, :base_url, :api_key)
                """
            ),
            {
                "id": llm_id,
                "name": name,
                "model": model,
                "base_url": base_url,
                "api_key": api_key,
            },
        )
        combo_to_id[key] = llm_id
    return combo_to_id


def _backfill_agent_model_ids(bind, combo_to_id: dict[tuple[str | None, str | None, str | None], str]) -> None:
    if not _has_column("agents", "model"):
        return
    agents = bind.execute(
        sa.text("SELECT id, model, base_url, api_key, model_id FROM agents")
    ).fetchall()
    for agent_id, model, base_url, api_key, model_id in agents:
        if model_id is not None:
            continue
        bind.execute(
            sa.text("UPDATE agents SET model_id = :model_id WHERE id = :agent_id"),
            {"model_id": combo_to_id[(model, base_url, api_key)], "agent_id": agent_id},
        )


def upgrade() -> None:
    bind = _bind()
    if not _has_table("llm_models"):
        op.create_table(
            "llm_models",
            sa.Column("id", sa.String(AGENT_ID_LENGTH), nullable=False),
            sa.Column("name", _key_type(), nullable=False),
            sa.Column("description", sa.Text(), nullable=True),
            sa.Column("model", sa.Text(), nullable=False),
            sa.Column("base_url", sa.Text(), nullable=True),
            sa.Column("api_key", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=_now_default(), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("name", name="uq_llm_models_name"),
        )

    combo_to_id = _ensure_llm_models(bind)

    if not _has_column("agents", "model_id"):
        op.add_column("agents", sa.Column("model_id", sa.String(AGENT_ID_LENGTH), nullable=True))
    if not _has_column("agents", "temperature"):
        op.add_column("agents", sa.Column("temperature", sa.Float(), nullable=True))

    _backfill_agent_model_ids(bind, combo_to_id)

    if is_sqlite(bind):
        needs_batch = (
            _column_nullable("agents", "model_id")
            or not _constraint_exists("agents", FK_NAME)
            or _has_column("agents", "model")
        )
        if needs_batch:
            with op.batch_alter_table("agents") as batch_op:
                if _column_nullable("agents", "model_id"):
                    batch_op.alter_column("model_id", nullable=False)
                if not _constraint_exists("agents", FK_NAME):
                    batch_op.create_foreign_key(
                        FK_NAME,
                        "llm_models",
                        ["model_id"],
                        ["id"],
                        ondelete="RESTRICT",
                    )
                if _has_column("agents", "model"):
                    batch_op.drop_column("model")
                    batch_op.drop_column("base_url")
                    batch_op.drop_column("api_key")
        return

    if _column_nullable("agents", "model_id"):
        op.alter_column(
            "agents",
            "model_id",
            existing_type=sa.String(AGENT_ID_LENGTH),
            nullable=False,
        )
    if not _constraint_exists("agents", FK_NAME):
        create_foreign_key(
            bind,
            FK_NAME,
            "agents",
            "llm_models",
            ["model_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    if _has_column("agents", "model"):
        op.drop_column("agents", "model")
        op.drop_column("agents", "base_url")
        op.drop_column("agents", "api_key")


def downgrade() -> None:
    bind = _bind()
    op.add_column("agents", sa.Column("model", sa.Text(), nullable=True))
    op.add_column("agents", sa.Column("base_url", sa.Text(), nullable=True))
    op.add_column("agents", sa.Column("api_key", sa.Text(), nullable=True))

    bind.execute(
        sa.text(
            """
            UPDATE agents
            SET model = (
                SELECT lm.model FROM llm_models lm WHERE lm.id = agents.model_id
            ),
            base_url = (
                SELECT lm.base_url FROM llm_models lm WHERE lm.id = agents.model_id
            ),
            api_key = (
                SELECT lm.api_key FROM llm_models lm WHERE lm.id = agents.model_id
            )
            """
        )
    )

    if is_sqlite(bind):
        with op.batch_alter_table("agents") as batch_op:
            batch_op.drop_constraint(FK_NAME, type_="foreignkey")
            batch_op.drop_column("model_id")
            batch_op.drop_column("temperature")
            batch_op.alter_column("model", nullable=False)
    else:
        op.drop_constraint(FK_NAME, "agents", type_="foreignkey")
        op.drop_column("agents", "model_id")
        op.drop_column("agents", "temperature")
        op.alter_column("agents", "model", nullable=False)

    op.drop_table("llm_models")
