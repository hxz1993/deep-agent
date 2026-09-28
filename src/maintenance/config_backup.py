from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import MetaData, Table, delete, func, insert, inspect, select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from deep_agent.capabilities.codes import canonical_tool_package_codes
from deep_agent.db.models import Base
from deep_agent.db.configuration_transfer import (
    BACKUP_SCOPE_SCHEMA_VERSION,
    SESSION_TABLES,
    STRUCTURE_ONLY_TABLES,
    backup_scope_manifest,
    configuration_table_names,
    order_configuration_tables,
    validate_table_classification,
)


CONFIG_TABLES = configuration_table_names()
ENVIRONMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# Backward-compatible private aliases used by existing maintenance tests.
_backup_scope_manifest = backup_scope_manifest
_validate_table_classification = validate_table_classification


def _structure_only_tables(revision: str) -> tuple[str, ...]:
    # Released 009 backups retain their source schema; current backups do not
    # create, copy or require the removed outbound receipt table.
    if revision == "009":
        return (*SESSION_TABLES, "channel_message_operations", *(
            name for name in STRUCTURE_ONLY_TABLES if name not in SESSION_TABLES
        ))
    return STRUCTURE_ONLY_TABLES


def _revision_backup_scope_manifest(copied_tables: Sequence[str], revision: str) -> dict[str, Any]:
    manifest = _backup_scope_manifest(copied_tables)
    if revision == "009":
        manifest["structure_only_tables"] = list(_structure_only_tables(revision))
    return manifest


@dataclass(frozen=True)
class BackupResult:
    path: Path
    revision: str
    row_counts: dict[str, int]


def repository_root() -> Path:
    from deep_agent.core.paths import REPO_ROOT

    return REPO_ROOT


def default_output_root() -> Path:
    return repository_root().parent / "deep-agent-config-backups"


def database_url_from_config(config_path: str | Path) -> str:
    path = Path(config_path).expanduser().resolve()
    if not path.is_file():
        raise FileNotFoundError(f"Config file does not exist: {path}")

    values = {
        key: value
        for key, value in dotenv_values(path).items()
        if value is not None
    }
    dialect = str(values.get("DEEP_AGENT_DBMS_NAME") or "").strip().lower()
    if not dialect:
        sqlite_path = str(values.get("DEEP_AGENT_SQLITE_PATH") or "").strip()
        if not sqlite_path:
            raise ValueError(
                "Config must define DEEP_AGENT_DBMS_NAME or DEEP_AGENT_SQLITE_PATH"
            )
        resolved = Path(sqlite_path).expanduser()
        if not resolved.is_absolute():
            resolved = (path.parent / resolved).resolve()
        return f"sqlite+aiosqlite:///{resolved.as_posix()}"

    driver_map = {
        "mysql": "mysql+asyncmy",
        "postgres": "postgresql+asyncpg",
    }
    drivername = driver_map.get(dialect)
    if drivername is None:
        raise ValueError(f"Unsupported database type for backup: {dialect}")

    required = {
        "username": "DEEP_AGENT_DBMS_USER",
        "password": "DEEP_AGENT_DBMS_PASSWORD",
        "host": "DEEP_AGENT_DBMS_HOSTNAME",
        "port": "DEEP_AGENT_DBMS_PORT",
        "database": "DEEP_AGENT_DBMS_DATABASE",
    }
    missing = [env_name for env_name in required.values() if not values.get(env_name)]
    if missing:
        raise ValueError(f"Config is missing database settings: {', '.join(missing)}")

    return URL.create(
        drivername=drivername,
        username=str(values[required["username"]]),
        password=str(values[required["password"]]),
        host=str(values[required["host"]]),
        port=int(str(values[required["port"]])),
        database=str(values[required["database"]]),
    ).render_as_string(hide_password=False)


async def export_backup(
    source_database_url: str,
    *,
    environment: str,
    output_root: str | Path | None = None,
    exported_at: datetime | None = None,
    git_commit: str | None = None,
) -> BackupResult:
    if not ENVIRONMENT_PATTERN.fullmatch(environment):
        raise ValueError(
            "Environment may contain only letters, numbers, underscores, and hyphens"
        )

    _validate_table_classification()
    source_engine = create_async_engine(source_database_url, pool_pre_ping=True)
    target_engine: AsyncEngine | None = None
    partial_path: Path | None = None
    try:
        revision = await _source_revision(source_engine)
        # Configuration exports omit session rows. Record the source physical
        # variant without migrating it; the empty target uses current revision DDL.
        source_schema = await _session_source_schema(source_engine)
        backup_time = exported_at or datetime.now().astimezone()
        timestamp = backup_time.strftime("%Y%m%d-%H%M%S")
        revision_label = re.sub(r"[^A-Za-z0-9_-]", "_", revision)
        target_dir = Path(output_root or default_output_root()).expanduser().resolve() / environment
        target_dir.mkdir(parents=True, exist_ok=True)
        target_path = target_dir / (
            f"deep-agent-config-{environment}-v{revision_label}-{timestamp}.db"
        )
        if target_path.exists():
            raise FileExistsError(f"Backup already exists: {target_path}")

        partial_path = target_path.with_name(
            target_path.stem + ".partial-TMP-to-delete.db"
        )
        partial_path.unlink(missing_ok=True)
        target_url = f"sqlite+aiosqlite:///{partial_path.as_posix()}"
        await asyncio.to_thread(_upgrade_target_schema, target_url, revision)
        target_engine = create_async_engine(target_url)
        copied_tables = await _discover_copied_tables(target_engine)
        target_tables = await _database_table_names(target_engine)
        structure_only_tables = tuple(name for name in _structure_only_tables(revision) if name in target_tables)
        await _validate_source_tables(source_engine, copied_tables)
        await _create_backup_info_table(target_engine)
        await _clear_seed_configuration(target_engine, copied_tables)
        row_counts: dict[str, int] = {}
        for table_name in copied_tables:
            row_counts[table_name] = await _copy_table(
                source_engine,
                target_engine,
                table_name,
            )

        await _write_backup_metadata(
            target_engine,
            environment=environment,
            exported_at=backup_time,
            source_dialect=source_engine.dialect.name,
            revision=revision,
            git_commit=git_commit or _current_git_commit(),
            copied_tables=copied_tables,
            source_schema=source_schema,
        )
        await _validate_backup(
            target_engine,
            revision=revision,
            expected_counts=row_counts,
            copied_tables=copied_tables,
            expected_source_schema=source_schema,
        )
        await target_engine.dispose()
        target_engine = None

        await _validate_with_deep_agent(target_url)
        os.chmod(partial_path, 0o600)
        partial_path.replace(target_path)
        return BackupResult(
            path=target_path,
            revision=revision,
            row_counts={
                **row_counts,
                **{table_name: 0 for table_name in structure_only_tables},
            },
        )
    except Exception:
        if target_engine is not None:
            await target_engine.dispose()
        if partial_path is not None:
            partial_path.unlink(missing_ok=True)
        raise
    finally:
        await source_engine.dispose()


async def _source_revision(engine: AsyncEngine) -> str:
    async with engine.connect() as connection:
        result = await connection.execute(
            text("SELECT version_num FROM alembic_version")
        )
        revision = result.scalar_one_or_none()
    if not revision:
        raise ValueError("Source database does not contain an Alembic revision")
    return str(revision)


async def _session_source_schema(engine: AsyncEngine) -> dict[str, bool]:
    """Record physical source facts, independently of the shared 010 revision."""
    async with engine.connect() as connection:
        columns = await connection.run_sync(lambda conn: {
            item["name"] for item in inspect(conn).get_columns("chat_sessions")
        })
    return {name: name in columns for name in ("source_type", "source_id")}


def _upgrade_target_schema(database_url: str, revision: str = "head") -> None:
    config = Config(str(repository_root() / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root() / "migrations"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, revision)


async def _database_table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )


async def _discover_copied_tables(engine: AsyncEngine) -> tuple[str, ...]:
    table_names = await _database_table_names(engine)
    missing_session_tables = await _required_session_tables(engine) - table_names
    if missing_session_tables:
        raise ValueError(
            "Backup schema is missing session tables: "
            + ", ".join(sorted(missing_session_tables))
        )

    copied_names = table_names - set(_structure_only_tables(await _source_revision(engine))) - {"alembic_version"}
    return order_configuration_tables(tuple(copied_names))


async def _validate_source_tables(
    engine: AsyncEngine,
    copied_tables: Sequence[str],
) -> None:
    source_tables = await _database_table_names(engine)
    required_tables = set(copied_tables) | await _required_session_tables(engine) | {"alembic_version"}
    missing = required_tables - source_tables
    if missing:
        raise ValueError(
            "Source database is missing tables required by its Alembic revision: "
            + ", ".join(sorted(missing))
        )


async def _required_session_tables(engine: AsyncEngine) -> set[str]:
    required = set(SESSION_TABLES)
    revision = await _source_revision(engine)
    if revision == "009":
        required.add("channel_message_operations")
    return required


async def _create_backup_info_table(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                CREATE TABLE _backup_info (
                    id INTEGER NOT NULL PRIMARY KEY,
                    environment VARCHAR(64) NOT NULL,
                    exported_at VARCHAR(64) NOT NULL,
                    source_dialect VARCHAR(32) NOT NULL,
                    alembic_revision VARCHAR(32) NOT NULL,
                    git_commit VARCHAR(64) NOT NULL,
                    scope_manifest TEXT NOT NULL,
                    source_session_schema TEXT NOT NULL
                )
                """
            )
        )


async def _clear_seed_configuration(
    engine: AsyncEngine,
    copied_tables: Sequence[str],
) -> None:
    async with engine.begin() as connection:
        for table_name in reversed(copied_tables):
            table = await connection.run_sync(
                lambda sync_connection, name=table_name: Table(
                    name,
                    MetaData(),
                    autoload_with=sync_connection,
                )
            )
            await connection.execute(delete(table))


async def _reflect_table(engine: AsyncEngine, table_name: str) -> Table:
    metadata = MetaData()
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_connection: Table(
                table_name,
                metadata,
                autoload_with=sync_connection,
            )
        )


async def _copy_table(
    source_engine: AsyncEngine,
    target_engine: AsyncEngine,
    table_name: str,
) -> int:
    source_table = await _reflect_table(source_engine, table_name)
    target_table = await _reflect_table(target_engine, table_name)
    async with source_engine.connect() as source_connection:
        result = await source_connection.execute(select(source_table))
        rows = [dict(row) for row in result.mappings().all()]
    if table_name == "llm_providers":
        for row in rows:
            if row.get("auth_method") == "oauth":
                row["credential_json"] = None
                row["auth_status"] = "unconfigured"
                row["auth_revision"] = 0
    if table_name == "agents":
        for row in rows:
            raw_capabilities = row.get("capabilities") or []
            serialized = isinstance(raw_capabilities, str)
            if serialized:
                raw_capabilities = json.loads(raw_capabilities)
            canonical = canonical_tool_package_codes(raw_capabilities)
            row["capabilities"] = (
                json.dumps(canonical, ensure_ascii=False, separators=(",", ":"))
                if serialized
                else canonical
            )
    if rows:
        async with target_engine.begin() as target_connection:
            await target_connection.execute(insert(target_table), rows)
    return len(rows)


async def _write_backup_metadata(
    engine: AsyncEngine,
    *,
    environment: str,
    exported_at: datetime,
    source_dialect: str,
    revision: str,
    git_commit: str,
    copied_tables: Sequence[str],
    source_schema: Mapping[str, bool],
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO _backup_info (
                    id, environment, exported_at, source_dialect,
                    alembic_revision, git_commit, scope_manifest, source_session_schema
                ) VALUES (
                    1, :environment, :exported_at, :source_dialect,
                    :revision, :git_commit, :scope_manifest, :source_session_schema
                )
                """
            ),
            {
                "environment": environment,
                "exported_at": exported_at.isoformat(),
                "source_dialect": source_dialect,
                "revision": revision,
                "git_commit": git_commit,
                "source_session_schema": json.dumps(dict(source_schema), separators=(",", ":")),
                "scope_manifest": json.dumps(
                    _revision_backup_scope_manifest(copied_tables, revision),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ),
            },
        )


async def _validate_backup(
    engine: AsyncEngine,
    *,
    revision: str,
    expected_counts: Mapping[str, int],
    copied_tables: Sequence[str],
    expected_source_schema: Mapping[str, bool] | None = None,
) -> None:
    async with engine.connect() as connection:
        integrity = await connection.execute(text("PRAGMA integrity_check"))
        if integrity.scalar_one() != "ok":
            raise ValueError("SQLite integrity check failed")

        for table_name, expected in expected_counts.items():
            actual = await _table_count(connection, table_name)
            if actual != expected:
                raise ValueError(
                    f"Backup row count mismatch for {table_name}: "
                    f"expected {expected}, got {actual}"
                )
        table_names = await connection.run_sync(lambda sync: set(inspect(sync).get_table_names()))
        for table_name in _structure_only_tables(revision):
            if table_name in table_names and await _table_count(connection, table_name) != 0:
                raise ValueError(f"Structure-only table must be empty: {table_name}")

        # PRAGMA foreign_key_check is insufficient after 003. Audit the
        # logical relations, including the configured channel Agent reference.
        from deep_agent.db.reference_schema import assert_logical_references
        await connection.run_sync(
            lambda sync: assert_logical_references(sync, tables=set(copied_tables))
        )

        stored_revision = (
            await connection.execute(
                text("SELECT version_num FROM alembic_version")
            )
        ).scalar_one_or_none()
        if stored_revision != revision:
            raise ValueError("Backup Alembic revision does not match the source")

        info = (
            await connection.execute(
                text(
                    "SELECT environment, exported_at, source_dialect, "
                    "alembic_revision, git_commit, scope_manifest, source_session_schema "
                    "FROM _backup_info WHERE id = 1"
                )
            )
        ).mappings().one()
        if not all(info.values()) or info["alembic_revision"] != revision:
            raise ValueError("Backup metadata is incomplete")
        source_schema = json.loads(str(info["source_session_schema"]))
        if (not isinstance(source_schema, dict)
                or set(source_schema) != {"source_type", "source_id"}
                or any(type(value) is not bool for value in source_schema.values())):
            raise ValueError("Backup source session schema metadata is invalid")
        if expected_source_schema is not None and source_schema != dict(expected_source_schema):
            raise ValueError("Backup source session schema does not match the source")
        scope_manifest = json.loads(str(info["scope_manifest"]))
        if scope_manifest != _revision_backup_scope_manifest(copied_tables, revision):
            raise ValueError("Backup scope manifest does not match the current contract")


async def _table_count(connection: Any, table_name: str) -> int:
    result = await connection.execute(
        text(f'SELECT COUNT(*) FROM "{table_name}"')
    )
    return int(result.scalar_one())


async def _validate_with_deep_agent(database_url: str) -> None:
    from deep_agent.db.factory import create_engine
    from deep_agent.db.models import (
        ChatSessionEvent,
        LlmModel,
        LlmProvider,
        ModelCatalog,
        UiToolPackage,
        SkillPackage,
    )

    engine, session_factory = create_engine(database_url)
    try:
        async with session_factory() as session:
            await session.execute(select(func.count()).select_from(LlmModel))
            await session.execute(select(func.count()).select_from(LlmProvider))
            orphan_models = int(
                (
                    await session.execute(
                        select(func.count())
                        .select_from(LlmModel)
                        .outerjoin(LlmProvider, LlmProvider.id == LlmModel.provider_id)
                        .where(LlmProvider.id.is_(None))
                    )
                ).scalar_one()
            )
            if orphan_models:
                raise ValueError(
                    "Backup contains LLM models with missing Provider references"
                )
            await session.execute(select(func.count()).select_from(ModelCatalog))
            await session.execute(select(func.count()).select_from(UiToolPackage))
            await session.execute(select(func.count()).select_from(SkillPackage))
            await session.execute(select(func.count()).select_from(ChatSessionEvent))
    finally:
        await engine.dispose()


def _current_git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export DeepAgent configuration to one SQLite database file.",
    )
    parser.add_argument(
        "--config",
        required=True,
        help="External dotenv file containing the source database settings.",
    )
    parser.add_argument(
        "--environment",
        required=True,
        help="Environment label used in the output directory and filename.",
    )
    parser.add_argument(
        "--output-root",
        default=None,
        help="Optional backup root; defaults to ../deep-agent-config-backups.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> None:
    args = _parser().parse_args(argv)
    result = asyncio.run(
        export_backup(
            database_url_from_config(args.config),
            environment=args.environment,
            output_root=args.output_root,
        )
    )
    counts = ", ".join(
        f"{table}={count}" for table, count in result.row_counts.items()
    )
    print(f"Backup created: {result.path}")
    print(f"Alembic revision: {result.revision}")
    print(f"Rows: {counts}")
    structure_only = ", ".join(table for table in STRUCTURE_ONLY_TABLES if table in result.row_counts)
    print(f"Structure only (no data copied): {structure_only}")
    print(
        "External Skill ZIP files are not embedded; use Console Skill > "
        "Export all for a portable companion package."
    )


if __name__ == "__main__":
    main()
