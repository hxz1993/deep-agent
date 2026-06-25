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

from deep_agent.db.migration_dialect import (
    create_foreign_key,
    is_sqlite,
    key_type,
    now_default,
)

revision: str = "013"
down_revision: Union[str, Sequence[str], None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

AGENT_ID_LENGTH = 36


def _bind():
    return op.get_bind()


def _key_type():
    return key_type(_bind())


def _now_default():
    return now_default(_bind())


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


def _seed_llm_models(bind) -> dict[tuple[str | None, str | None, str | None], str]:
    rows = bind.execute(
        sa.text("SELECT DISTINCT model, base_url, api_key FROM agents ORDER BY model, base_url, api_key")
    ).fetchall()
    used_names: set[str] = set()
    combo_to_id: dict[tuple[str | None, str | None, str | None], str] = {}
    for model, base_url, api_key in rows:
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
        combo_to_id[(model, base_url, api_key)] = llm_id
    return combo_to_id


def _backfill_agent_model_ids(bind, combo_to_id: dict[tuple[str | None, str | None, str | None], str]) -> None:
    agents = bind.execute(sa.text("SELECT id, model, base_url, api_key FROM agents")).fetchall()
    for agent_id, model, base_url, api_key in agents:
        model_id = combo_to_id[(model, base_url, api_key)]
        bind.execute(
            sa.text("UPDATE agents SET model_id = :model_id WHERE id = :agent_id"),
            {"model_id": model_id, "agent_id": agent_id},
        )


def upgrade() -> None:
    bind = _bind()
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

    combo_to_id = _seed_llm_models(bind)

    op.add_column("agents", sa.Column("model_id", sa.String(AGENT_ID_LENGTH), nullable=True))
    op.add_column("agents", sa.Column("temperature", sa.Float(), nullable=True))

    _backfill_agent_model_ids(bind, combo_to_id)

    if is_sqlite(bind):
        with op.batch_alter_table("agents") as batch_op:
            batch_op.alter_column("model_id", nullable=False)
            batch_op.create_foreign_key(
                "agents_model_id_fkey",
                "llm_models",
                ["model_id"],
                ["id"],
                ondelete="RESTRICT",
            )
            batch_op.drop_column("model")
            batch_op.drop_column("base_url")
            batch_op.drop_column("api_key")
        return

    op.alter_column("agents", "model_id", nullable=False)
    create_foreign_key(
        bind,
        "agents_model_id_fkey",
        "agents",
        "llm_models",
        ["model_id"],
        ["id"],
        ondelete="RESTRICT",
    )
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
            batch_op.drop_constraint("agents_model_id_fkey", type_="foreignkey")
            batch_op.drop_column("model_id")
            batch_op.drop_column("temperature")
            batch_op.alter_column("model", nullable=False)
    else:
        op.drop_constraint("agents_model_id_fkey", "agents", type_="foreignkey")
        op.drop_column("agents", "model_id")
        op.drop_column("agents", "temperature")
        op.alter_column("agents", "model", nullable=False)

    op.drop_table("llm_models")
