"""Replace physical foreign keys with application-managed references.

Requires a maintenance window: stop every old API and gateway writer first.
Revision ID: 003
Revises: 002
"""
from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa

revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None

# Frozen 002 schema. Do not import the evolving ORM or current reference manifest.
_REFERENCES = (
    ("llm_models", "provider_id", "llm_providers", "id", "RESTRICT"),
    ("agents", "model_id", "llm_models", "id", "RESTRICT"),
    ("chat_sessions", "agent_id", "agents", "id", "RESTRICT"),
    ("ui_tool_definitions", "package_code", "ui_tool_packages", "code", "RESTRICT"),
    ("chat_session_events", "session_id", "chat_sessions", "session_id", "CASCADE"),
    ("channel_accounts", "agent_id", "agents", "id", "RESTRICT"),
    ("channel_session_bindings", "account_id", "channel_accounts", "id", "CASCADE"),
    ("channel_session_bindings", "session_id", "chat_sessions", "session_id", "SET NULL"),
    ("channel_message_runs", "account_id", "channel_accounts", "id", "CASCADE"),
    ("channel_message_runs", "binding_id", "channel_session_bindings", "id", "CASCADE"),
)
# Remove children's FKs before rebuilding a referenced parent on SQLite. Keeping
# foreign_keys ON is safe in this order, including CASCADE and SET NULL edges.
_CHILD_FIRST = (
    "channel_message_runs", "channel_session_bindings", "chat_session_events",
    "chat_sessions", "channel_accounts", "agents", "ui_tool_definitions", "llm_models",
)
_NAMING = {"fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s"}


def _preflight(bind):
    failures = []
    for table, column, parent_table, parent_column, _ in _REFERENCES:
        child = sa.table(table, sa.column(column))
        parent = sa.table(parent_table, sa.column(parent_column))
        count = bind.scalar(sa.select(sa.func.count()).select_from(child).where(
            child.c[column].is_not(None),
            ~sa.exists(sa.select(1).select_from(parent).where(parent.c[parent_column] == child.c[column])),
        ))
        if count:
            failures.append(f"{table}.{column} -> {parent_table}.{parent_column}: {count} orphan(s)")
    if failures:
        raise RuntimeError("003 logical reference preflight failed: " + "; ".join(failures)
                           + ". No data was repaired or deleted; remain in maintenance mode.")


def _matching(foreign_key, reference):
    _, column, parent_table, parent_column, _ = reference
    return (foreign_key["constrained_columns"] == [column]
            and foreign_key["referred_table"] == parent_table
            and foreign_key["referred_columns"] == [parent_column])


def _name(reference):
    # Short deterministic names also fit PostgreSQL's identifier limit.
    table, column, _, _, _ = reference
    historical = {
        ("llm_models", "provider_id"): "fk_llm_models_provider_id_llm_providers",
        ("agents", "model_id"): "agents_model_id_fkey",
        ("chat_sessions", "agent_id"): "fk_chat_sessions_agent_id_agents",
        ("ui_tool_definitions", "package_code"): "ui_tool_definitions_package_code_fkey",
        ("chat_session_events", "session_id"): "fk_chat_session_events_session_id_sessions",
    }
    return historical.get((table, column), f"fk_{table}_{column}")


def _alter(*, restore):
    if context.is_offline_mode():
        raise RuntimeError("003 requires an online database connection for reference preflight and actual constraint discovery; offline SQL is not supported")
    bind = op.get_bind()
    _preflight(bind)
    order = reversed(_CHILD_FIRST) if restore else _CHILD_FIRST
    for table in order:
        references = [item for item in _REFERENCES if item[0] == table]
        foreign_keys = sa.inspect(bind).get_foreign_keys(table)
        pending = []
        for reference in references:
            matches = [fk for fk in foreign_keys if _matching(fk, reference)]
            if restore:
                if not matches:
                    pending.append((reference, None))
            else:
                pending.extend((reference, fk) for fk in matches)
        if not pending:
            continue
        # Re-introspection on every run makes MySQL's nontransactional DDL
        # resumable after any successful DROP. Never advance the revision early.
        if bind.dialect.name == "sqlite":
            triggers = bind.execute(sa.text(
                "SELECT name, sql FROM sqlite_master WHERE type='trigger' AND tbl_name=:table"
            ), {"table": table}).all()
            with op.batch_alter_table(table, recreate="always", naming_convention=_NAMING) as batch:
                for reference, fk in pending:
                    if restore:
                        batch.create_foreign_key(_name(reference), reference[2], [reference[1]], [reference[3]], ondelete=reference[4])
                    else:
                        name = fk["name"] or f"fk_{table}_{reference[1]}_{reference[2]}"
                        batch.drop_constraint(name, type_="foreignkey")
            for _, sql in triggers:
                if sql:
                    bind.exec_driver_sql(sql)
        else:
            for reference, fk in pending:
                if restore:
                    name = _name(reference)
                    if bind.dialect.name == "mysql":
                        # MySQL may replace an implicit supporting index when
                        # restoring an FK with another name. Reuse a supporting
                        # index name, including names customized by operations.
                        indexes = sa.inspect(bind).get_indexes(table)
                        supporting = sorted(
                            (index for index in indexes if index["column_names"][:1] == [reference[1]]),
                            key=lambda index: (len(index["column_names"]), index["name"]),
                        )
                        if supporting:
                            name = supporting[0]["name"]
                    op.create_foreign_key(name, table, reference[2], [reference[1]], [reference[3]], ondelete=reference[4])
                else:
                    if not fk["name"]:
                        raise RuntimeError(f"Cannot resolve foreign key name for {table}.{reference[1]}; remain in maintenance mode")
                    op.drop_constraint(fk["name"], table, type_="foreignkey")
    _preflight(bind)
    if bind.dialect.name == "sqlite":
        integrity = bind.exec_driver_sql("PRAGMA integrity_check").scalars().all()
        if integrity != ["ok"] or bind.exec_driver_sql("PRAGMA foreign_key_check").first() is not None:
            raise RuntimeError("003 SQLite integrity check failed; remain in maintenance mode")


def upgrade():
    _alter(restore=False)


def downgrade():
    _alter(restore=True)
