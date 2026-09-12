"""Logical references shared by maintenance tools (independent of ORM foreign keys)."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

import sqlalchemy as sa


@dataclass(frozen=True)
class Relation:
    table: str
    column: str
    parent_table: str
    parent_column: str
    ondelete: str


LOGICAL_REFERENCES = (
    Relation("llm_models", "provider_id", "llm_providers", "id", "RESTRICT"),
    Relation("agents", "model_id", "llm_models", "id", "RESTRICT"),
    Relation("chat_sessions", "agent_id", "agents", "id", "RESTRICT"),
    Relation("ui_tool_definitions", "package_code", "ui_tool_packages", "code", "RESTRICT"),
    Relation("chat_session_events", "session_id", "chat_sessions", "session_id", "CASCADE"),
    Relation("channel_accounts", "agent_id", "agents", "id", "RESTRICT"),
    Relation("channel_session_bindings", "account_id", "channel_accounts", "id", "CASCADE"),
    Relation("channel_session_bindings", "session_id", "chat_sessions", "session_id", "SET NULL"),
    Relation("channel_message_runs", "account_id", "channel_accounts", "id", "CASCADE"),
    Relation("channel_message_runs", "binding_id", "channel_session_bindings", "id", "CASCADE"),
)


def logical_reference_violations(connection, tables: Iterable[str] | None = None) -> list[dict]:
    """Count orphans, excluding nullable references; never return customer values."""
    selected = None if tables is None else set(tables)
    violations = []
    for relation in LOGICAL_REFERENCES:
        if selected is not None and relation.table not in selected:
            continue
        child = sa.table(relation.table, sa.column(relation.column))
        parent = sa.table(relation.parent_table, sa.column(relation.parent_column))
        value = child.c[relation.column]
        count = connection.scalar(
            sa.select(sa.func.count()).select_from(child).where(
                value.is_not(None),
                ~sa.exists(sa.select(1).select_from(parent).where(
                    parent.c[relation.parent_column] == value,
                )),
            )
        )
        if count:
            violations.append({"table": relation.table, "column": relation.column,
                               "parent_table": relation.parent_table,
                               "parent_column": relation.parent_column, "count": int(count)})
    return violations


def assert_logical_references(connection, tables: Iterable[str] | None = None) -> None:
    violations = logical_reference_violations(connection, tables)
    if violations:
        detail = "; ".join(
            f"{item['table']}.{item['column']} -> "
            f"{item['parent_table']}.{item['parent_column']}: {item['count']} orphan(s)"
            for item in violations
        )
        raise RuntimeError(f"Logical reference validation failed: {detail}. No data was repaired or deleted.")
