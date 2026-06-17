"""store json columns as text for database portability

Revision ID: 007
Revises: 006
Create Date: 2026-06-17

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import mysql, postgresql

revision: str = "007"
down_revision: Union[str, Sequence[str], None] = "006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_JSON_COLUMNS: tuple[tuple[str, str, bool], ...] = (
    ("agents", "capabilities", True),
    ("mcp_servers", "config", False),
    ("chat_messages", "payload", False),
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
    target_type = _text_storage_type()
    existing_type = _existing_json_type()
    dialect = _dialect_name()

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
    target_type = _existing_json_type()
    dialect = _dialect_name()
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
