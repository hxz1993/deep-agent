"""ui_tool_definitions: store json columns as text for database portability

Revision ID: 010
Revises: 009
Create Date: 2026-06-23

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql, postgresql

revision: str = "010"
down_revision: Union[str, Sequence[str], None] = "009"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("ui_tool_definitions", "parameters_json_schema", False),
    ("ui_tool_definitions", "result_schema", True),
    ("ui_tool_definitions", "metadata", True),
    ("ui_tool_definitions", "agent_ids", True),
)


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _text_storage_type():
    if _dialect_name() == "mysql":
        return mysql.LONGTEXT()
    return sa.Text()


def _existing_json_type():
    if _dialect_name() == "postgresql":
        return postgresql.JSONB(astext_type=sa.Text())
    if _dialect_name() == "mysql":
        return sa.JSON()
    return sa.JSON()


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect not in {"postgresql", "mysql"}:
        return

    target_type = _text_storage_type()
    existing_type = _existing_json_type()

    for table, column, nullable in _JSON_COLUMNS:
        kwargs: dict = {
            "existing_type": existing_type,
            "existing_nullable": nullable,
            "type_": target_type,
        }
        if dialect == "postgresql":
            kwargs["postgresql_using"] = f"{column}::text"
        op.alter_column(table, column, **kwargs)


def downgrade() -> None:
    dialect = _dialect_name()
    if dialect not in {"postgresql", "mysql"}:
        return

    target_type = _existing_json_type()
    if dialect == "mysql":
        text_type = mysql.LONGTEXT()
    else:
        text_type = sa.Text()

    for table, column, nullable in _JSON_COLUMNS:
        kwargs: dict = {
            "existing_type": text_type,
            "existing_nullable": nullable,
            "type_": target_type,
        }
        if dialect == "postgresql":
            kwargs["postgresql_using"] = f"{column}::jsonb"
        op.alter_column(table, column, **kwargs)
