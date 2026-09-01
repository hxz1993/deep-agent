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

from deep_agent.db.models import Base
from deep_agent.db.configuration_transfer import (
    BACKUP_SCOPE_SCHEMA_VERSION,
    SESSION_TABLES,
    backup_scope_manifest,
    configuration_table_names,
    validate_table_classification,
)


CONFIG_TABLES = configuration_table_names()
ENVIRONMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")

# Backward-compatible private aliases used by existing maintenance tests.
_backup_scope_manifest = backup_scope_manifest
_validate_table_classification = validate_table_classification


@dataclass(frozen=True)
class BackupResult:
    path: Path
    revision: str
    row_counts: dict[str, int]


def repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


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
        )
        await _validate_backup(
            target_engine,
            revision=revision,
            expected_counts=row_counts,
            copied_tables=copied_tables,
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
                **{table_name: 0 for table_name in SESSION_TABLES},
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


def _upgrade_target_schema(database_url: str, revision: str = "head") -> None:
    config = Config(str(repository_root() / "alembic.ini"))
    config.set_main_option("script_location", str(repository_root() / "alembic"))
    config.attributes["database_url"] = database_url
    command.upgrade(config, revision)


async def _database_table_names(engine: AsyncEngine) -> set[str]:
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_connection: set(inspect(sync_connection).get_table_names())
        )


async def _discover_copied_tables(engine: AsyncEngine) -> tuple[str, ...]:
    table_names = await _database_table_names(engine)
    missing_session_tables = set(SESSION_TABLES) - table_names
    if missing_session_tables:
        raise ValueError(
            "Backup schema is missing session tables: "
            + ", ".join(sorted(missing_session_tables))
        )

    copied_names = table_names - set(SESSION_TABLES) - {"alembic_version"}
    metadata = MetaData()
    async with engine.connect() as connection:
        await connection.run_sync(
            lambda sync_connection: metadata.reflect(
                bind=sync_connection,
                only=sorted(copied_names),
            )
        )
    return tuple(table.name for table in metadata.sorted_tables)


async def _validate_source_tables(
    engine: AsyncEngine,
    copied_tables: Sequence[str],
) -> None:
    source_tables = await _database_table_names(engine)
    required_tables = set(copied_tables) | set(SESSION_TABLES) | {"alembic_version"}
    missing = required_tables - source_tables
    if missing:
        raise ValueError(
            "Source database is missing tables required by its Alembic revision: "
            + ", ".join(sorted(missing))
        )


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
                    scope_manifest TEXT NOT NULL
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
) -> None:
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO _backup_info (
                    id, environment, exported_at, source_dialect,
                    alembic_revision, git_commit, scope_manifest
                ) VALUES (
                    1, :environment, :exported_at, :source_dialect,
                    :revision, :git_commit, :scope_manifest
                )
                """
            ),
            {
                "environment": environment,
                "exported_at": exported_at.isoformat(),
                "source_dialect": source_dialect,
                "revision": revision,
                "git_commit": git_commit,
                "scope_manifest": json.dumps(
                    _backup_scope_manifest(copied_tables),
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
        for table_name in SESSION_TABLES:
            if await _table_count(connection, table_name) != 0:
                raise ValueError(f"Session table must be empty: {table_name}")

        orphan_models = int(
            (
                await connection.execute(
                    text(
                        "SELECT COUNT(*) FROM llm_models AS model "
                        "LEFT JOIN llm_providers AS provider "
                        "ON provider.id = model.provider_id "
                        "WHERE provider.id IS NULL"
                    )
                )
            ).scalar_one()
        )
        if orphan_models:
            raise ValueError(
                "Backup contains LLM models with missing Provider references"
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
                    "alembic_revision, git_commit, scope_manifest "
                    "FROM _backup_info WHERE id = 1"
                )
            )
        ).mappings().one()
        if not all(info.values()) or info["alembic_revision"] != revision:
            raise ValueError("Backup metadata is incomplete")
        scope_manifest = json.loads(str(info["scope_manifest"]))
        if scope_manifest != _backup_scope_manifest(copied_tables):
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
    print(
        "External Skill ZIP files are not embedded; use Console Skill > "
        "Export all for a portable companion package."
    )


if __name__ == "__main__":
    main()
