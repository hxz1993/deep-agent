"""Separate online Skills from tool packages.

Revision ID: 003
Revises: 002
Create Date: 2026-08-11
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from typing import Any, Union

import sqlalchemy as sa
from alembic import op

from deep_agent.db.migration_dialect import boolean_default, key_type, now_default, text_json_type


revision: str = "003"
down_revision: Union[str, Sequence[str], None] = "002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _bind():
    return op.get_bind()


def _key_type():
    return key_type(_bind())


def _json_type():
    return text_json_type(_bind())


def _skills_column(*, nullable: bool) -> sa.Column:
    return sa.Column("skills", _json_type(), nullable=nullable)


def _as_string_list(value: Any, *, field: str) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        value = json.loads(value)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise RuntimeError(f"{field} contains an invalid value: {value!r}")
    return value


def _validate_upgrade_data() -> None:
    """Reject legacy Skill capabilities before non-transactional MySQL DDL."""
    agents = sa.table(
        "agents",
        sa.column("id", sa.String(36)),
        sa.column("capabilities", _json_type()),
    )
    legacy: list[str] = []
    for row in _bind().execute(sa.select(agents.c.id, agents.c.capabilities)).mappings():
        values = _as_string_list(row["capabilities"], field="agents.capabilities")
        for value in values or []:
            if value.startswith("skill:"):
                legacy.append(f"{row['id']}={value}")
    if legacy:
        raise RuntimeError(
            "Migration 003 cannot convert legacy skill:* capabilities; remove or migrate them first: "
            + ", ".join(legacy)
        )


def upgrade() -> None:
    _validate_upgrade_data()

    op.create_table(
        "skill_packages",
        sa.Column("code", _key_type(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("version", sa.String(64), nullable=True),
        sa.Column("schema_version", sa.String(32), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column(
            "enabled",
            sa.Boolean(),
            server_default=boolean_default(_bind(), value=True),
            nullable=False,
        ),
        sa.Column("platform_file_id", _key_type(), nullable=False),
        sa.Column("file_name", sa.Text(), nullable=False),
        sa.Column("file_size", sa.BigInteger(), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False),
        sa.Column("manifest", _json_type(), nullable=False),
        sa.Column("resource_index", _json_type(), nullable=False),
        sa.Column(
            "contains_scripts",
            sa.Boolean(),
            server_default=boolean_default(_bind(), value=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=now_default(_bind()),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=now_default(_bind()),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("code"),
    )

    # MySQL maps the portable JSON storage type to LONGTEXT, which cannot have a
    # server default on the supported Test version. Add it nullable, backfill,
    # then enforce NOT NULL so all dialects share the same data semantics.
    with op.batch_alter_table("agents") as batch:
        batch.add_column(
            _skills_column(nullable=True)
        )
    op.execute(sa.text("UPDATE agents SET skills = '[]' WHERE skills IS NULL"))
    with op.batch_alter_table("agents") as batch:
        batch.alter_column(
            "skills",
            existing_type=_json_type(),
            nullable=False,
        )


def _validate_downgrade_data() -> None:
    bind = _bind()
    skill_packages = sa.table("skill_packages", sa.column("code", _key_type()))
    package_count = bind.execute(sa.select(sa.func.count()).select_from(skill_packages)).scalar_one()
    if int(package_count) > 0:
        raise RuntimeError("Migration 003 downgrade requires skill_packages to be empty")

    agents = sa.table(
        "agents",
        sa.column("id", sa.String(36)),
        sa.column("skills", _json_type()),
    )
    configured: list[str] = []
    for row in bind.execute(sa.select(agents.c.id, agents.c.skills)).mappings():
        values = _as_string_list(row["skills"], field="agents.skills") or []
        if values:
            configured.append(f"{row['id']}={values!r}")
    if configured:
        raise RuntimeError(
            "Migration 003 downgrade requires every agents.skills value to be empty: "
            + ", ".join(configured)
        )


def downgrade() -> None:
    _validate_downgrade_data()
    with op.batch_alter_table("agents") as batch:
        batch.drop_column("skills")
    op.drop_table("skill_packages")
