"""Separate LLM providers from model presets.

Revision ID: 007
Revises: 006
Create Date: 2026-08-24
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any, Union

import sqlalchemy as sa
from alembic import op


revision: str = "007"
down_revision: Union[str, Sequence[str], None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_DEFAULT_URLS = {
    "deepseek": "https://api.deepseek.com/",
    "moonshotai": "https://api.moonshot.ai/v1/",
    "alibaba": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/",
    "zai": "https://api.z.ai/api/paas/v4/",
    "openrouter": "https://openrouter.ai/api/v1/",
    "openai": "https://api.openai.com/v1/",
    "anthropic": "https://api.anthropic.com/",
    "google": "https://generativelanguage.googleapis.com/",
}
_LABELS = {
    "deepseek": "DeepSeek",
    "moonshotai": "MoonshotAI",
    "alibaba": "Alibaba",
    "zai": "Z.AI",
    "openrouter": "OpenRouter",
    "openai": "OpenAI",
    "anthropic": "Anthropic",
    "google": "Google Gemini",
    "openai_compatible": "OpenAI Compatible",
}


def _normalized_url(value: Any) -> str:
    return str(value or "").strip().rstrip("/")


def _infer_provider(model: str, base_url: Any) -> tuple[str, bool, str]:
    raw_model = (model or "").strip().lower()
    prefix = raw_model.split(":", 1)[0]
    url = _normalized_url(base_url)
    if raw_model.startswith("deepseek"):
        provider_type = "deepseek"
    elif raw_model.startswith(("moonshot", "kimi")):
        provider_type = "moonshotai"
    elif raw_model.startswith(("alibaba", "qwen", "dashscope")):
        provider_type = "alibaba"
    elif raw_model.startswith(("zai", "zhipu", "glm")):
        provider_type = "zai"
    elif prefix == "openrouter":
        provider_type = "openrouter"
    elif prefix == "anthropic":
        provider_type = "anthropic"
    elif prefix in {"google", "gemini"}:
        provider_type = "google"
    elif prefix == "openai" and (
        not url or url == _normalized_url(_DEFAULT_URLS["openai"])
    ):
        provider_type = "openai"
    elif url:
        provider_type = "openai_compatible"
    else:
        provider_type = "openai"

    default_url = _normalized_url(_DEFAULT_URLS.get(provider_type))
    use_default = bool(default_url) and (not url or url == default_url)
    resolved_url = default_url if use_default else url
    if not resolved_url:
        resolved_url = _normalized_url(_DEFAULT_URLS["openai"])
        provider_type = "openai"
        use_default = True
    return provider_type, use_default, resolved_url


def upgrade() -> None:
    op.create_table(
        "llm_providers",
        sa.Column("id", sa.String(length=32), nullable=False),
        sa.Column("name", sa.String(length=191), nullable=False),
        sa.Column("provider_type", sa.String(length=64), nullable=False),
        sa.Column("use_default_base_url", sa.Boolean(), nullable=False),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("api_key", sa.Text(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_llm_providers_name"),
    )
    with op.batch_alter_table("llm_models") as batch_op:
        batch_op.add_column(sa.Column("provider_id", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column(
                "enabled",
                sa.Boolean(),
                nullable=False,
                server_default=sa.true(),
            )
        )

    bind = op.get_bind()
    metadata = sa.MetaData()
    models = sa.Table("llm_models", metadata, autoload_with=bind)
    providers = sa.Table("llm_providers", metadata, autoload_with=bind)
    rows = bind.execute(
        sa.select(models.c.id, models.c.model, models.c.base_url, models.c.api_key)
        .order_by(models.c.id)
    ).mappings().all()
    identities: dict[tuple[str, bool, str, str | None], str] = {}
    label_counts: dict[str, int] = {}
    for row in rows:
        provider_type, use_default, resolved_url = _infer_provider(
            str(row["model"]), row["base_url"]
        )
        api_key = str(row["api_key"]) if row["api_key"] is not None else None
        identity = (provider_type, use_default, resolved_url, api_key)
        provider_id = identities.get(identity)
        if provider_id is None:
            provider_id = uuid.uuid4().hex
            identities[identity] = provider_id
            base_name = _LABELS[provider_type]
            label_counts[base_name] = label_counts.get(base_name, 0) + 1
            suffix = label_counts[base_name]
            name = base_name if suffix == 1 else f"{base_name} {suffix}"
            bind.execute(
                providers.insert().values(
                    id=provider_id,
                    name=name,
                    provider_type=provider_type,
                    use_default_base_url=use_default,
                    base_url=resolved_url,
                    api_key=api_key,
                    enabled=bool(api_key),
                    created_at=sa.func.now(),
                    updated_at=sa.func.now(),
                )
            )
        bind.execute(
            models.update()
            .where(models.c.id == row["id"])
            .values(provider_id=provider_id)
        )

    orphan_rows = bind.execute(
        sa.select(models.c.id, models.c.name)
        .select_from(
            models.outerjoin(providers, models.c.provider_id == providers.c.id)
        )
        .where(
            sa.or_(
                models.c.provider_id.is_(None),
                providers.c.id.is_(None),
            )
        )
        .order_by(models.c.id)
    ).mappings().all()
    if orphan_rows:
        identifiers = ", ".join(
            f"{row['id']} ({row['name']})" for row in orphan_rows
        )
        raise RuntimeError(
            "Cannot make llm_models.provider_id non-null; orphan models: "
            + identifiers
        )

    with op.batch_alter_table("llm_models") as batch_op:
        batch_op.alter_column("provider_id", existing_type=sa.String(length=32), nullable=False)
        batch_op.drop_constraint("uq_llm_models_name", type_="unique")
        batch_op.create_unique_constraint(
            "uq_llm_models_provider_name",
            ["provider_id", "name"],
        )
        batch_op.create_foreign_key(
            "fk_llm_models_provider_id_llm_providers",
            "llm_providers",
            ["provider_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_index("ix_llm_models_provider_id", ["provider_id"], unique=False)
        batch_op.drop_column("base_url")
        batch_op.drop_column("api_key")


def downgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    models = sa.Table("llm_models", metadata, autoload_with=bind)
    providers = sa.Table("llm_providers", metadata, autoload_with=bind)
    duplicate_names = bind.execute(
        sa.select(models.c.name, sa.func.count())
        .group_by(models.c.name)
        .having(sa.func.count() > 1)
        .order_by(models.c.name)
    ).all()
    if duplicate_names:
        names = ", ".join(str(name) for name, _count in duplicate_names)
        raise RuntimeError(
            "Cannot downgrade to global model-name uniqueness; duplicate names: "
            + names
        )

    with op.batch_alter_table("llm_models") as batch_op:
        batch_op.add_column(sa.Column("base_url", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("api_key", sa.Text(), nullable=True))

    metadata = sa.MetaData()
    models = sa.Table("llm_models", metadata, autoload_with=bind)
    providers = sa.Table("llm_providers", metadata, autoload_with=bind)
    rows = bind.execute(
        sa.select(models.c.id, providers.c.base_url, providers.c.api_key).select_from(
            models.join(providers, models.c.provider_id == providers.c.id)
        )
    ).mappings().all()
    for row in rows:
        bind.execute(
            models.update()
            .where(models.c.id == row["id"])
            .values(base_url=row["base_url"], api_key=row["api_key"])
        )
    with op.batch_alter_table("llm_models") as batch_op:
        batch_op.drop_constraint(
            "fk_llm_models_provider_id_llm_providers",
            type_="foreignkey",
        )
        batch_op.drop_index("ix_llm_models_provider_id")
        batch_op.drop_constraint("uq_llm_models_provider_name", type_="unique")
        batch_op.drop_column("provider_id")
        batch_op.drop_column("enabled")
        batch_op.create_unique_constraint("uq_llm_models_name", ["name"])
    op.drop_table("llm_providers")
