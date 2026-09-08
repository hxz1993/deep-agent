from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import mysql, postgresql


def dialect_name(bind) -> str:
    return bind.dialect.name


def is_postgresql(bind) -> bool:
    return dialect_name(bind) == "postgresql"


def is_mysql(bind) -> bool:
    return dialect_name(bind) == "mysql"


def is_sqlite(bind) -> bool:
    return dialect_name(bind) == "sqlite"


def has_column(bind, table_name: str, column_name: str) -> bool:
    return column_name in {col["name"] for col in inspect(bind).get_columns(table_name)}


def has_table(bind, table_name: str) -> bool:
    return table_name in inspect(bind).get_table_names()


def constraint_exists(bind, table_name: str, constraint_name: str) -> bool:
    insp = inspect(bind)
    pk = insp.get_pk_constraint(table_name)
    if pk.get("name") == constraint_name:
        return True
    for uc in insp.get_unique_constraints(table_name):
        if uc.get("name") == constraint_name:
            return True
    for fk in insp.get_foreign_keys(table_name):
        if fk.get("name") == constraint_name:
            return True
    return False


def key_type(bind):
    if is_postgresql(bind):
        return sa.Text()
    return sa.String(191)


def json_type(bind):
    if is_postgresql(bind):
        return postgresql.JSONB(astext_type=sa.Text())
    return sa.JSON()


def text_json_type(bind):
    if is_mysql(bind):
        return mysql.LONGTEXT()
    return sa.Text()


def existing_json_type(bind):
    if is_postgresql(bind):
        return postgresql.JSONB(astext_type=sa.Text())
    if is_mysql(bind):
        return sa.JSON()
    return sa.JSON()


def now_default(bind):
    if is_postgresql(bind):
        return sa.text("now()")
    return sa.text("CURRENT_TIMESTAMP")


def boolean_default(bind, *, value: bool):
    if is_postgresql(bind):
        return sa.text("true" if value else "false")
    return sa.text("1" if value else "0")


_DEFAULT_AGENT_PROMPT = (
    "You are deep-agent, a helpful general-purpose assistant. "
    "Use the available tools when they help you answer accurately. "
    "Be concise and explain your reasoning when it is useful."
)


def insert_default_agent_statement(bind) -> str:
    cols = "(name, model, prompt)"
    values = f"VALUES ('default', 'openai:gpt-4o', '{_DEFAULT_AGENT_PROMPT}')"
    if is_postgresql(bind):
        return f"INSERT INTO agents {cols} {values} ON CONFLICT (name) DO NOTHING"
    if is_sqlite(bind):
        return f"INSERT OR IGNORE INTO agents {cols} {values}"
    return f"INSERT IGNORE INTO agents {cols} {values}"


def session_id_from_key_expr(column: str = "session_key", *, bind) -> str:
    if is_postgresql(bind):
        return (
            f"CASE WHEN position(':' in {column}) > 0 "
            f"THEN split_part({column}, ':', 2) ELSE {column} END"
        )
    return (
        f"CASE WHEN INSTR({column}, ':') > 0 "
        f"THEN SUBSTR({column}, INSTR({column}, ':') + 1) ELSE {column} END"
    )


def agent_prefix_from_key_expr(column: str = "session_key", *, bind) -> str:
    if is_postgresql(bind):
        return (
            f"CASE WHEN position(':' in {column}) > 0 "
            f"THEN split_part({column}, ':', 1) ELSE '' END"
        )
    return (
        f"CASE WHEN INSTR({column}, ':') > 0 "
        f"THEN SUBSTR({column}, 1, INSTR({column}, ':') - 1) ELSE '' END"
    )


def random_agent_id_expr(bind) -> str:
    if is_postgresql(bind):
        return "gen_random_uuid()"
    if is_sqlite(bind):
        return "lower(hex(randomblob(16)))"
    return "REPLACE(UUID(), '-', '')"


def agents_primary_key_columns(bind, table_name: str = "agents") -> list[str]:
    return list(inspect(bind).get_pk_constraint(table_name).get("constrained_columns", []))


def rebuild_agents_primary_key_sqlite(bind, *, pk_column: str = "id") -> None:
    """Rebuild agents without batch_alter_table — avoids SAWarning vs Base.metadata."""
    if agents_primary_key_columns(bind) == [pk_column]:
        return
    if pk_column == "id" and not has_column(bind, "agents", "id"):
        raise RuntimeError("agents.id must exist before PK swap to id")

    id_col = (
        "id VARCHAR(36) NOT NULL,"
        if pk_column == "id"
        else "id VARCHAR(36),"
    )
    name_col = (
        "name VARCHAR(191) NOT NULL UNIQUE,"
        if pk_column == "id"
        else "name VARCHAR(191) NOT NULL,"
    )
    pk_clause = f"PRIMARY KEY ({pk_column})"

    op.execute(
        sa.text(
            f"""
            CREATE TABLE _agents_pk_swap (
                {id_col}
                {name_col}
                model TEXT NOT NULL,
                base_url TEXT,
                api_key TEXT,
                capabilities TEXT,
                prompt TEXT NOT NULL,
                created_at DATETIME NOT NULL,
                updated_at DATETIME NOT NULL,
                {pk_clause}
            )
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO _agents_pk_swap
                (id, name, model, base_url, api_key, capabilities, prompt, created_at, updated_at)
            SELECT id, name, model, base_url, api_key, capabilities, prompt, created_at, updated_at
            FROM agents
            """
        )
    )
    op.execute(sa.text("DROP TABLE agents"))
    op.execute(sa.text("ALTER TABLE _agents_pk_swap RENAME TO agents"))


def find_foreign_key_name(
    bind,
    table_name: str,
    column_name: str,
    *,
    referenced_table: str | None = None,
) -> str | None:
    for fk in inspect(bind).get_foreign_keys(table_name):
        if column_name not in fk.get("constrained_columns", []):
            continue
        if referenced_table is not None and fk.get("referred_table") != referenced_table:
            continue
        return fk.get("name")
    return None


def drop_foreign_key(bind, table_name: str, constraint_name: str) -> None:
    if is_sqlite(bind):
        with op.batch_alter_table(table_name) as batch_op:
            batch_op.drop_constraint(constraint_name, type_="foreignkey")
        return
    op.drop_constraint(constraint_name, table_name, type_="foreignkey")


def create_foreign_key(
    bind,
    constraint_name: str,
    source_table: str,
    referent_table: str,
    local_cols: list[str],
    remote_cols: list[str],
    *,
    ondelete: str | None = None,
) -> None:
    if is_sqlite(bind):
        with op.batch_alter_table(source_table) as batch_op:
            batch_op.create_foreign_key(
                constraint_name,
                referent_table,
                local_cols,
                remote_cols,
                ondelete=ondelete,
            )
        return
    op.create_foreign_key(
        constraint_name,
        source_table,
        referent_table,
        local_cols,
        remote_cols,
        ondelete=ondelete,
    )


def insert_or_ignore(
    bind,
    table: sa.TableClause,
    values: dict,
    *,
    conflict_columns: list[str],
) -> None:
    """Insert a row; skip silently when unique/PK columns already exist."""
    if is_postgresql(bind):
        stmt = (
            postgresql.insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict_columns)
        )
    elif is_sqlite(bind):
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert

        stmt = (
            sqlite_insert(table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=conflict_columns)
        )
    else:
        from sqlalchemy.dialects.mysql import insert as mysql_insert

        stmt = mysql_insert(table).values(**values).prefix_with("IGNORE")
    bind.execute(stmt)
