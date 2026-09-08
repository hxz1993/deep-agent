from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable, Mapping

from alembic import command
from alembic.config import Config
from sqlalchemy import MetaData, Table, func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from maintenance.config_backup import database_url_from_config, repository_root


TARGET_REVISION = "001"
LEGACY_REVISION = "012"

EXPECTED_COLUMNS: dict[str, tuple[str, ...]] = {
    "global_settings": (
        "key", "value", "version", "created_at", "updated_at",
    ),
    "llm_providers": (
        "id", "name", "provider_type", "use_default_base_url", "base_url",
        "api_key", "created_at", "updated_at",
    ),
    "llm_models": (
        "id", "provider_id", "name", "description", "model",
        "input_modalities", "context_window_tokens", "max_output_tokens",
        "tags", "enabled", "created_at", "updated_at",
    ),
    "model_catalog": (
        "provider_id", "provider_model_id", "provider_label", "provider_npm",
        "provider_api_url", "provider_doc_url", "lab_id", "lab_label",
        "lab_description", "canonical_model_id", "model_label",
        "model_description", "family", "release_date",
        "canonical_context_window_tokens", "canonical_max_output_tokens",
        "context_window_tokens", "max_output_tokens", "input_modalities",
        "output_modalities", "capabilities", "status", "lab_metadata",
        "model_metadata", "provider_metadata", "source", "refreshed_at",
        "created_at", "updated_at",
    ),
    "agents": (
        "id", "name", "model_id", "temperature", "capabilities", "skills",
        "prompt", "created_at", "updated_at",
    ),
    "skill_packages": (
        "code", "label", "description", "version", "schema_version", "source",
        "platform_file_id", "file_name", "file_size", "content_hash", "manifest",
        "resource_index", "contains_scripts", "created_at", "updated_at",
    ),
    "mcp_servers": (
        "name", "transport", "config", "inspection", "description",
        "created_at", "updated_at",
    ),
    "ui_tool_packages": (
        "code", "label", "description", "source", "created_at", "updated_at",
    ),
    "ui_tool_definitions": (
        "name", "package_code", "description", "parameters_json_schema",
        "widget", "result_schema", "metadata", "source", "created_at",
        "updated_at",
    ),
    "chat_sessions": (
        "session_id", "agent_id", "user_id", "app_id", "space_id", "title",
        "next_sequence", "created_at", "updated_at",
    ),
    "chat_session_events": (
        "event_id", "session_id", "sequence", "schema_version", "event_type",
        "status", "turn_id", "run_id", "search_text", "payload", "created_at",
        "updated_at",
    ),
}

EXPECTED_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "global_settings": ("key",),
    "llm_providers": ("id",),
    "llm_models": ("id",),
    "model_catalog": ("provider_id", "provider_model_id"),
    "agents": ("id",),
    "skill_packages": ("code",),
    "mcp_servers": ("name",),
    "ui_tool_packages": ("code",),
    "ui_tool_definitions": ("name",),
    "chat_sessions": ("session_id",),
    "chat_session_events": ("event_id",),
}

EXPECTED_UNIQUES: dict[str, set[tuple[str, ...]]] = {
    "llm_providers": {("name",)},
    "llm_models": {("provider_id", "name")},
    "agents": {("name",)},
    "chat_session_events": {("session_id", "sequence")},
}

EXPECTED_INDEXES: dict[str, dict[str, tuple[str, ...]]] = {
    "model_catalog": {
        "ix_model_catalog_lab_id": ("lab_id",),
        "ix_model_catalog_canonical_model_id": ("canonical_model_id",),
        "ix_model_catalog_provider_id": ("provider_id",),
        "ix_model_catalog_model_label": ("model_label",),
    },
    "llm_models": {
        "ix_llm_models_provider_id": ("provider_id",),
    },
    "ui_tool_definitions": {
        "ix_ui_tool_definitions_package_code": ("package_code",),
    },
    "chat_sessions": {
        "ix_chat_sessions_agent_user_updated": ("agent_id", "user_id", "updated_at"),
    },
    "chat_session_events": {
        "ix_chat_session_events_session_turn_sequence": ("session_id", "turn_id", "sequence"),
        "ix_chat_session_events_session_run_sequence": ("session_id", "run_id", "sequence"),
        "ix_chat_session_events_session_type_status_sequence": (
            "session_id", "event_type", "status", "sequence",
        ),
    },
}

EXPECTED_FOREIGN_KEYS = {
    "llm_models": {
        (("provider_id",), "llm_providers", ("id",), "RESTRICT"),
    },
    "agents": {
        (("model_id",), "llm_models", ("id",), "RESTRICT"),
    },
    "ui_tool_definitions": {
        (("package_code",), "ui_tool_packages", ("code",), "RESTRICT"),
    },
    "chat_sessions": {
        (("agent_id",), "agents", ("id",), "RESTRICT"),
    },
    "chat_session_events": {
        (("session_id",), "chat_sessions", ("session_id",), "CASCADE"),
    },
}

NULLABLE_COLUMNS = {
    ("llm_providers", "api_key"),
    *(('llm_models', name) for name in (
        "description", "input_modalities", "context_window_tokens",
        "max_output_tokens", "tags",
    )),
    *(('model_catalog', name) for name in (
        "provider_npm", "provider_api_url", "provider_doc_url", "lab_description",
        "model_description", "family", "release_date", "status",
    )),
    ("agents", "temperature"),
    ("skill_packages", "version"),
    ("mcp_servers", "inspection"),
    ("mcp_servers", "description"),
    ("ui_tool_packages", "description"),
    ("ui_tool_definitions", "widget"),
    ("ui_tool_definitions", "result_schema"),
    ("ui_tool_definitions", "metadata"),
    ("chat_sessions", "app_id"),
    ("chat_sessions", "space_id"),
    ("chat_sessions", "title"),
    ("chat_session_events", "status"),
    ("chat_session_events", "turn_id"),
    ("chat_session_events", "run_id"),
    ("chat_session_events", "search_text"),
}

INTEGER_COLUMNS = {
    ("global_settings", "version"),
    ("llm_models", "context_window_tokens"),
    ("llm_models", "max_output_tokens"),
    ("model_catalog", "canonical_context_window_tokens"),
    ("model_catalog", "canonical_max_output_tokens"),
    ("model_catalog", "context_window_tokens"),
    ("model_catalog", "max_output_tokens"),
    ("skill_packages", "file_size"),
    ("chat_sessions", "next_sequence"),
    ("chat_session_events", "sequence"),
    ("chat_session_events", "schema_version"),
}
FLOAT_COLUMNS = {("agents", "temperature")}
BOOLEAN_COLUMNS = {
    ("llm_providers", "use_default_base_url"),
    ("llm_models", "enabled"),
    ("skill_packages", "contains_scripts"),
}
DATETIME_COLUMNS = {
    (table_name, column_name)
    for table_name, columns in EXPECTED_COLUMNS.items()
    for column_name in columns
    if column_name in {"created_at", "updated_at", "refreshed_at"}
}
DEFAULT_REQUIRED_COLUMNS = {
    (table_name, column_name)
    for table_name, columns in EXPECTED_COLUMNS.items()
    for column_name in columns
    if column_name in {"created_at", "updated_at"}
} | {
    ("global_settings", "version"),
    ("llm_models", "enabled"),
    ("skill_packages", "contains_scripts"),
    ("chat_sessions", "next_sequence"),
}


class SchemaValidationError(RuntimeError):
    pass


@dataclass(frozen=True)
class DatabaseState:
    dialect: str
    revision: str
    schema: dict[str, Any]
    row_counts: dict[str, int]
    content_hashes: dict[str, str]


def _type_family(type_name: str) -> str:
    upper = type_name.upper()
    if "BOOL" in upper or "TINYINT" in upper:
        return "boolean"
    if "INT" in upper:
        return "integer"
    if any(item in upper for item in ("FLOAT", "DOUBLE", "REAL", "NUMERIC", "DECIMAL")):
        return "float"
    if any(item in upper for item in ("DATE", "TIME")):
        return "datetime"
    if any(item in upper for item in ("CHAR", "TEXT", "CLOB", "JSON", "UUID")):
        return "text"
    return upper.lower()


def _expected_type_family(table_name: str, column_name: str) -> str:
    key = (table_name, column_name)
    if key in INTEGER_COLUMNS:
        return "integer"
    if key in FLOAT_COLUMNS:
        return "float"
    if key in BOOLEAN_COLUMNS:
        return "boolean"
    if key in DATETIME_COLUMNS:
        return "datetime"
    return "text"


def _normalize_ondelete(value: Any) -> str | None:
    if value is None:
        return None
    return str(value).strip().upper().replace("_", " ")


def _inspect_schema(sync_connection) -> dict[str, Any]:
    inspector = inspect(sync_connection)
    table_names = sorted(inspector.get_table_names())
    schema: dict[str, Any] = {"table_names": table_names, "tables": {}}
    for table_name in sorted(EXPECTED_COLUMNS):
        if table_name not in table_names:
            continue
        schema["tables"][table_name] = {
            "columns": [
                {
                    "name": item["name"],
                    "type": str(item["type"]),
                    "nullable": bool(item.get("nullable", True)),
                    "default": None if item.get("default") is None else str(item["default"]),
                }
                for item in inspector.get_columns(table_name)
            ],
            "primary_key": list(
                inspector.get_pk_constraint(table_name).get("constrained_columns") or []
            ),
            "unique_constraints": [
                {
                    "name": item.get("name"),
                    "columns": list(item.get("column_names") or []),
                }
                for item in inspector.get_unique_constraints(table_name)
            ],
            "indexes": [
                {
                    "name": item.get("name"),
                    "columns": list(item.get("column_names") or []),
                    "unique": bool(item.get("unique", False)),
                }
                for item in inspector.get_indexes(table_name)
            ],
            "foreign_keys": [
                {
                    "name": item.get("name"),
                    "columns": list(item.get("constrained_columns") or []),
                    "referred_table": item.get("referred_table"),
                    "referred_columns": list(item.get("referred_columns") or []),
                    "ondelete": _normalize_ondelete((item.get("options") or {}).get("ondelete")),
                }
                for item in inspector.get_foreign_keys(table_name)
            ],
        }
    return schema


def validate_schema(schema: Mapping[str, Any]) -> None:
    errors: list[str] = []
    actual_tables = set(schema.get("table_names") or [])
    expected_tables = set(EXPECTED_COLUMNS) | {"alembic_version"}
    missing_tables = sorted(expected_tables - actual_tables)
    unexpected_tables = sorted(actual_tables - expected_tables)
    if missing_tables:
        errors.append(f"missing tables: {', '.join(missing_tables)}")
    if unexpected_tables:
        errors.append(f"unexpected tables: {', '.join(unexpected_tables)}")

    actual_table_data = schema.get("tables") or {}
    for table_name, expected_columns in EXPECTED_COLUMNS.items():
        table = actual_table_data.get(table_name)
        if table is None:
            continue
        columns = {item["name"]: item for item in table["columns"]}
        actual_column_names = set(columns)
        expected_column_names = set(expected_columns)
        missing = sorted(expected_column_names - actual_column_names)
        extra = sorted(actual_column_names - expected_column_names)
        if missing:
            errors.append(f"{table_name}: missing columns {', '.join(missing)}")
        if extra:
            errors.append(f"{table_name}: unexpected columns {', '.join(extra)}")
        for column_name in sorted(expected_column_names & actual_column_names):
            column = columns[column_name]
            expected_nullable = (table_name, column_name) in NULLABLE_COLUMNS
            if bool(column["nullable"]) != expected_nullable:
                errors.append(
                    f"{table_name}.{column_name}: nullable={column['nullable']} expected {expected_nullable}"
                )
            expected_family = _expected_type_family(table_name, column_name)
            actual_family = _type_family(str(column["type"]))
            if actual_family != expected_family:
                errors.append(
                    f"{table_name}.{column_name}: type {column['type']} is {actual_family}, "
                    f"expected {expected_family}"
                )
            if (
                (table_name, column_name) in DEFAULT_REQUIRED_COLUMNS
                and column.get("default") is None
            ):
                errors.append(f"{table_name}.{column_name}: missing server default")

        actual_pk = tuple(table.get("primary_key") or ())
        if actual_pk != EXPECTED_PRIMARY_KEYS[table_name]:
            errors.append(
                f"{table_name}: primary key {actual_pk!r} expected "
                f"{EXPECTED_PRIMARY_KEYS[table_name]!r}"
            )

        actual_uniques = {
            tuple(item.get("columns") or ())
            for item in table.get("unique_constraints") or []
        }
        actual_uniques.update(
            tuple(item.get("columns") or ())
            for item in table.get("indexes") or []
            if item.get("unique")
        )
        for columns_tuple in EXPECTED_UNIQUES.get(table_name, set()):
            if columns_tuple not in actual_uniques:
                errors.append(f"{table_name}: missing unique constraint on {columns_tuple!r}")

        actual_indexes = {
            item.get("name"): tuple(item.get("columns") or ())
            for item in table.get("indexes") or []
        }
        for index_name, index_columns in EXPECTED_INDEXES.get(table_name, {}).items():
            if actual_indexes.get(index_name) != index_columns:
                errors.append(
                    f"{table_name}: index {index_name} is {actual_indexes.get(index_name)!r}, "
                    f"expected {index_columns!r}"
                )

        actual_foreign_keys = {
            (
                tuple(item.get("columns") or ()),
                item.get("referred_table"),
                tuple(item.get("referred_columns") or ()),
                item.get("ondelete"),
            )
            for item in table.get("foreign_keys") or []
        }
        for expected_fk in EXPECTED_FOREIGN_KEYS.get(table_name, set()):
            if expected_fk in actual_foreign_keys:
                continue
            if expected_fk[-1] == "RESTRICT" and (*expected_fk[:-1], None) in actual_foreign_keys:
                continue
            errors.append(f"{table_name}: missing foreign key {expected_fk!r}")

    if errors:
        raise SchemaValidationError(
            "Database does not match the DeepAgent v0.6.6 baseline:\n- " + "\n- ".join(errors)
        )


def _stable_value(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return {"bytes_sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, Mapping):
        return {str(key): _stable_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_stable_value(item) for item in value]
    return value


async def _table_state(connection, table_name: str) -> tuple[int, str]:
    metadata = MetaData()
    table = await connection.run_sync(
        lambda sync_connection: Table(table_name, metadata, autoload_with=sync_connection)
    )
    count = int(
        (await connection.execute(select(func.count()).select_from(table))).scalar_one()
    )
    statement = select(table)
    primary_key = list(table.primary_key.columns)
    if primary_key:
        statement = statement.order_by(*primary_key)
    digest = hashlib.sha256()
    result = await connection.stream(statement)
    async for row in result.mappings():
        payload = [_stable_value(row[column_name]) for column_name in EXPECTED_COLUMNS[table_name]]
        digest.update(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode("utf-8")
        )
        digest.update(b"\n")
    return count, digest.hexdigest()


async def capture_database_state(engine: AsyncEngine) -> DatabaseState:
    async with engine.connect() as connection:
        revision = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one_or_none()
        if revision is None:
            raise SchemaValidationError("Database does not contain an Alembic revision")
        schema = await connection.run_sync(_inspect_schema)
        validate_schema(schema)
        row_counts: dict[str, int] = {}
        content_hashes: dict[str, str] = {}
        for table_name in sorted(EXPECTED_COLUMNS):
            count, digest = await _table_state(connection, table_name)
            row_counts[table_name] = count
            content_hashes[table_name] = digest
    return DatabaseState(
        dialect=engine.dialect.name,
        revision=str(revision),
        schema=schema,
        row_counts=row_counts,
        content_hashes=content_hashes,
    )


def _alembic_config(database_url: str) -> Config:
    root = repository_root()
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "migrations"))
    config.attributes["database_url"] = database_url
    return config


async def rebaseline_database(
    database_url: str,
    *,
    expected_revision: str = LEGACY_REVISION,
    stamp: bool = False,
) -> tuple[DatabaseState, DatabaseState | None]:
    engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        before = await capture_database_state(engine)
    finally:
        await engine.dispose()
    if before.revision != expected_revision:
        raise SchemaValidationError(
            f"Database revision is {before.revision}, expected {expected_revision}"
        )
    if not stamp:
        return before, None

    await asyncio.to_thread(
        command.stamp,
        _alembic_config(database_url),
        TARGET_REVISION,
        purge=True,
    )
    after_engine = create_async_engine(database_url, pool_pre_ping=True)
    try:
        after = await capture_database_state(after_engine)
    finally:
        await after_engine.dispose()
    if after.revision != TARGET_REVISION:
        raise SchemaValidationError(
            f"Rebaseline stamp ended at {after.revision}, expected {TARGET_REVISION}"
        )
    if before.schema != after.schema:
        raise SchemaValidationError("Schema changed while stamping the database")
    if before.row_counts != after.row_counts:
        raise SchemaValidationError("Row counts changed while stamping the database")
    if before.content_hashes != after.content_hashes:
        raise SchemaValidationError("Table contents changed while stamping the database")
    return before, after


def write_report(
    path: str | Path,
    *,
    before: DatabaseState,
    after: DatabaseState | None,
) -> Path:
    report_path = Path(path).expanduser().resolve()
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(
            {
                "target_revision": TARGET_REVISION,
                "before": asdict(before),
                "after": None if after is None else asdict(after),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(report_path, 0o600)
    return report_path


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Validate a revision-012 DeepAgent v0.6.6 schema and optionally "
            "re-stamp it as the production revision 001 baseline."
        )
    )
    parser.add_argument("--config", required=True, help="External DeepAgent env file")
    parser.add_argument(
        "--expected-revision",
        default=LEGACY_REVISION,
        help="Revision required before validation/stamp (default: 012)",
    )
    parser.add_argument("--report", help="Write a schema/count/hash report without row data")
    parser.add_argument("--stamp", action="store_true", help="Stamp the validated database as 001")
    parser.add_argument("--yes", action="store_true", help="Required together with --stamp")
    return parser


async def _run_cli(args: argparse.Namespace) -> int:
    if args.stamp and not args.yes:
        raise SystemExit("--stamp requires --yes")
    before, after = await rebaseline_database(
        database_url_from_config(args.config),
        expected_revision=args.expected_revision,
        stamp=args.stamp,
    )
    report_path = None
    if args.report:
        report_path = write_report(args.report, before=before, after=after)
    print(
        f"Schema valid: dialect={before.dialect}, revision={before.revision}, "
        f"tables={len(before.row_counts)}"
    )
    if after is not None:
        print(f"Database re-stamped: {before.revision} -> {after.revision}")
        print("Schema, row counts, and content hashes are unchanged")
    if report_path is not None:
        print(f"Report written: {report_path}")
    return 0


def main(argv: Iterable[str] | None = None) -> int:
    return asyncio.run(_run_cli(_build_parser().parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
