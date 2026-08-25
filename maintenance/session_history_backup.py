from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy import MetaData, Table, insert, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from deep_agent.db.serialization import deserialize_message
from maintenance.config_backup import (
    ENVIRONMENT_PATTERN,
    database_url_from_config,
    repository_root,
)


LEGACY_SESSION_REVISION = "007"
BACKUP_FORMAT_VERSION = "1.0"
LEGACY_SESSION_TABLES = (
    "chat_sessions",
    "chat_messages",
    "chat_context_checkpoints",
)
JSON_COLUMNS = {
    "chat_messages": {"payload"},
    "chat_context_checkpoints": {"payload"},
}
PRIMARY_KEY_COLUMNS = {
    "chat_sessions": ("agent_id", "session_id"),
    "chat_messages": ("id",),
    "chat_context_checkpoints": ("agent_id", "session_id"),
}
ARTIFACT_KEY_KINDS = {
    "artifact_id": "tool_overflow",
    "platform_file_id": "platform_file",
    "file_id": "platform_file",
    "attachment_id": "attachment",
}


@dataclass(frozen=True)
class SessionHistoryBackupResult:
    path: Path
    checksum_path: Path
    revision: str
    row_counts: dict[str, int]
    table_hashes: dict[str, str]


def default_output_root() -> Path:
    return repository_root().parent / "deep-agent-session-backups"


async def export_session_history_backup(
    source_database_url: str,
    *,
    environment: str,
    output_root: str | Path | None = None,
    exported_at: datetime | None = None,
    git_commit: str | None = None,
) -> SessionHistoryBackupResult:
    if not ENVIRONMENT_PATTERN.fullmatch(environment):
        raise ValueError(
            "Environment may contain only letters, numbers, underscores, and hyphens"
        )

    source_engine = create_async_engine(source_database_url, pool_pre_ping=True)
    target_engine: AsyncEngine | None = None
    partial_path: Path | None = None
    checksum_partial_path: Path | None = None
    try:
        revision = await _source_revision(source_engine)
        if revision != LEGACY_SESSION_REVISION:
            raise ValueError(
                f"Session history source revision must be {LEGACY_SESSION_REVISION}, "
                f"got {revision}"
            )
        await _validate_source_tables(source_engine)

        backup_time = exported_at or datetime.now().astimezone()
        timestamp = backup_time.strftime("%Y%m%d-%H%M%S")
        target_dir = (
            Path(output_root or default_output_root()).expanduser().resolve()
            / environment
        )
        target_dir.mkdir(parents=True, exist_ok=True)
        os.chmod(target_dir, 0o700)
        target_path = target_dir / (
            f"deep-agent-sessions-{environment}-v{revision}-{timestamp}.db"
        )
        checksum_path = target_path.with_suffix(target_path.suffix + ".sha256")
        if target_path.exists() or checksum_path.exists():
            raise FileExistsError(f"Backup already exists: {target_path}")

        partial_path = target_path.with_name(
            target_path.stem + ".partial-TMP-to-delete.db"
        )
        checksum_partial_path = checksum_path.with_name(
            checksum_path.name + ".partial-TMP-to-delete"
        )
        partial_path.unlink(missing_ok=True)
        checksum_partial_path.unlink(missing_ok=True)

        target_url = f"sqlite+aiosqlite:///{partial_path.as_posix()}"
        target_engine = create_async_engine(target_url)
        await _create_archive_schema(target_engine)

        rows_by_table: dict[str, list[dict[str, Any]]] = {}
        row_counts: dict[str, int] = {}
        table_hashes: dict[str, str] = {}
        for table_name in LEGACY_SESSION_TABLES:
            rows = await _read_source_rows(source_engine, table_name)
            rows_by_table[table_name] = rows
            row_counts[table_name] = len(rows)
            table_hashes[table_name] = _rows_hash(rows)
            await _write_archive_rows(target_engine, table_name, rows)

        anomalies = _find_legacy_relationship_anomalies(rows_by_table)
        await _validate_message_payloads(rows_by_table["chat_messages"])
        manifests = _build_session_manifests(rows_by_table)
        artifacts = _build_external_artifacts(rows_by_table)
        await _write_manifests(target_engine, manifests)
        await _write_external_artifacts(target_engine, artifacts)
        await _write_anomalies(target_engine, anomalies)
        await _write_backup_info(
            target_engine,
            environment=environment,
            exported_at=backup_time,
            source_dialect=source_engine.dialect.name,
            revision=revision,
            git_commit=git_commit or _current_git_commit(),
            row_counts=row_counts,
            table_hashes=table_hashes,
            anomaly_count=len(anomalies),
        )
        await _validate_archive(
            target_engine,
            revision=revision,
            expected_rows=rows_by_table,
            expected_manifests=manifests,
        )

        await target_engine.dispose()
        target_engine = None
        os.chmod(partial_path, 0o600)
        partial_path.replace(target_path)
        digest = await asyncio.to_thread(_file_sha256, target_path)
        checksum_partial_path.write_text(
            f"{digest}  {target_path.name}\n",
            encoding="ascii",
        )
        os.chmod(checksum_partial_path, 0o600)
        checksum_partial_path.replace(checksum_path)
        return SessionHistoryBackupResult(
            path=target_path,
            checksum_path=checksum_path,
            revision=revision,
            row_counts=row_counts,
            table_hashes=table_hashes,
        )
    except Exception:
        if target_engine is not None:
            await target_engine.dispose()
        if partial_path is not None:
            partial_path.unlink(missing_ok=True)
        if checksum_partial_path is not None:
            checksum_partial_path.unlink(missing_ok=True)
        raise
    finally:
        await source_engine.dispose()


async def _source_revision(engine: AsyncEngine) -> str:
    async with engine.connect() as connection:
        revision = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one_or_none()
    if not revision:
        raise ValueError("Source database does not contain an Alembic revision")
    return str(revision)


async def _validate_source_tables(engine: AsyncEngine) -> None:
    async with engine.connect() as connection:
        existing = set(
            await connection.run_sync(
                lambda sync_connection: inspect(sync_connection).get_table_names()
            )
        )
    missing = set(LEGACY_SESSION_TABLES) - existing
    if missing:
        raise ValueError(
            "Source database is missing legacy session tables: "
            + ", ".join(sorted(missing))
        )


async def _create_archive_schema(engine: AsyncEngine) -> None:
    statements = (
        """
        CREATE TABLE alembic_version (
            version_num VARCHAR(32) NOT NULL PRIMARY KEY
        )
        """,
        """
        CREATE TABLE chat_sessions (
            agent_id VARCHAR(36) NOT NULL,
            session_id VARCHAR(191) NOT NULL,
            user_id VARCHAR(128),
            title TEXT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (agent_id, session_id)
        )
        """,
        """
        CREATE TABLE chat_messages (
            id BIGINT NOT NULL PRIMARY KEY,
            agent_id VARCHAR(36) NOT NULL,
            session_id VARCHAR(191) NOT NULL,
            sequence INTEGER NOT NULL,
            payload TEXT NOT NULL,
            created_at DATETIME NOT NULL,
            UNIQUE (agent_id, session_id, sequence)
        )
        """,
        """
        CREATE TABLE chat_context_checkpoints (
            agent_id VARCHAR(36) NOT NULL,
            session_id VARCHAR(191) NOT NULL,
            source_last_sequence INTEGER NOT NULL,
            source_history_hash VARCHAR(64) NOT NULL,
            payload TEXT NOT NULL,
            payload_schema_version INTEGER NOT NULL,
            projection_fingerprint VARCHAR(64) NOT NULL,
            estimated_tokens BIGINT NOT NULL,
            summary_model_id VARCHAR(36),
            row_version BIGINT NOT NULL,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            PRIMARY KEY (agent_id, session_id)
        )
        """,
        """
        CREATE TABLE _backup_info (
            id INTEGER NOT NULL PRIMARY KEY,
            format_version VARCHAR(32) NOT NULL,
            environment VARCHAR(64) NOT NULL,
            exported_at VARCHAR(64) NOT NULL,
            source_dialect VARCHAR(32) NOT NULL,
            alembic_revision VARCHAR(32) NOT NULL,
            git_commit VARCHAR(64) NOT NULL,
            table_manifest TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE _session_manifest (
            agent_id VARCHAR(36) NOT NULL,
            session_id VARCHAR(191) NOT NULL,
            user_id VARCHAR(128),
            title TEXT,
            created_at DATETIME NOT NULL,
            updated_at DATETIME NOT NULL,
            message_count BIGINT NOT NULL,
            max_sequence BIGINT,
            has_checkpoint BOOLEAN NOT NULL,
            source_session_present BOOLEAN NOT NULL,
            content_hash VARCHAR(64) NOT NULL,
            PRIMARY KEY (agent_id, session_id)
        )
        """,
        """
        CREATE TABLE _external_artifacts (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            agent_id VARCHAR(36) NOT NULL,
            session_id VARCHAR(191) NOT NULL,
            source_table VARCHAR(64) NOT NULL,
            source_row_id VARCHAR(191),
            source_sequence BIGINT,
            kind VARCHAR(64) NOT NULL,
            handle TEXT NOT NULL,
            availability VARCHAR(32) NOT NULL,
            locator_path TEXT NOT NULL
        )
        """,
        """
        CREATE TABLE _backup_anomalies (
            id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
            anomaly_type VARCHAR(64) NOT NULL,
            source_table VARCHAR(64) NOT NULL,
            agent_id VARCHAR(36) NOT NULL,
            session_id VARCHAR(191) NOT NULL,
            source_row_id VARCHAR(191),
            detail TEXT NOT NULL
        )
        """,
    )
    async with engine.begin() as connection:
        for statement in statements:
            await connection.execute(text(statement))
        await connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:revision)"),
            {"revision": LEGACY_SESSION_REVISION},
        )


async def _read_source_rows(
    engine: AsyncEngine,
    table_name: str,
) -> list[dict[str, Any]]:
    metadata = MetaData()
    async with engine.connect() as connection:
        table = await connection.run_sync(
            lambda sync_connection: Table(
                table_name,
                metadata,
                autoload_with=sync_connection,
            )
        )
        order_columns = [table.c[name] for name in PRIMARY_KEY_COLUMNS[table_name]]
        result = await connection.execute(select(table).order_by(*order_columns))
        rows = [dict(row) for row in result.mappings().all()]
    return [_normalize_row(table_name, row) for row in rows]


def _normalize_row(table_name: str, row: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(row)
    for column in JSON_COLUMNS.get(table_name, set()):
        value = normalized[column]
        if isinstance(value, str):
            value = json.loads(value)
        normalized[column] = value
    return normalized


async def _write_archive_rows(
    engine: AsyncEngine,
    table_name: str,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    if not rows:
        return
    metadata = MetaData()
    async with engine.begin() as connection:
        table = await connection.run_sync(
            lambda sync_connection: Table(
                table_name,
                metadata,
                autoload_with=sync_connection,
            )
        )
        encoded = []
        for row in rows:
            item = dict(row)
            for column in JSON_COLUMNS.get(table_name, set()):
                item[column] = _canonical_json(item[column])
            encoded.append(item)
        await connection.execute(insert(table), encoded)


def _find_legacy_relationship_anomalies(
    rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    sessions = {
        (str(row["agent_id"]), str(row["session_id"]))
        for row in rows_by_table["chat_sessions"]
    }
    anomalies: list[dict[str, Any]] = []
    for table_name in ("chat_messages", "chat_context_checkpoints"):
        for row in rows_by_table[table_name]:
            if (str(row["agent_id"]), str(row["session_id"])) in sessions:
                continue
            anomalies.append(
                {
                    "anomaly_type": "missing_session",
                    "source_table": table_name,
                    "agent_id": str(row["agent_id"]),
                    "session_id": str(row["session_id"]),
                    "source_row_id": str(row.get("id") or "") or None,
                    "detail": _canonical_json(
                        {
                            "sequence": row.get("sequence"),
                            "source_last_sequence": row.get("source_last_sequence"),
                        }
                    ),
                }
            )
    return anomalies


async def _validate_message_payloads(
    rows: Sequence[Mapping[str, Any]],
) -> None:
    for row in rows:
        payload = row["payload"]
        if not isinstance(payload, dict):
            raise ValueError("Legacy chat message payload must be a JSON object")
        try:
            deserialize_message(payload)
        except Exception as exc:
            raise ValueError(
                "Legacy chat message payload cannot be parsed "
                f"(message_id={row['id']})"
            ) from exc


def _build_session_manifests(
    rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    messages: dict[tuple[str, str], list[Mapping[str, Any]]] = {}
    checkpoints: dict[tuple[str, str], Mapping[str, Any]] = {}
    for row in rows_by_table["chat_messages"]:
        key = (str(row["agent_id"]), str(row["session_id"]))
        messages.setdefault(key, []).append(row)
    for row in rows_by_table["chat_context_checkpoints"]:
        checkpoints[(str(row["agent_id"]), str(row["session_id"]))] = row

    manifests: list[dict[str, Any]] = []
    session_keys: set[tuple[str, str]] = set()
    for session in rows_by_table["chat_sessions"]:
        key = (str(session["agent_id"]), str(session["session_id"]))
        session_keys.add(key)
        session_messages = sorted(messages.get(key, []), key=lambda row: int(row["sequence"]))
        checkpoint = checkpoints.get(key)
        content = {
            "session": session,
            "messages": session_messages,
            "checkpoint": checkpoint,
        }
        manifests.append(
            {
                "agent_id": key[0],
                "session_id": key[1],
                "user_id": session.get("user_id"),
                "title": session.get("title"),
                "created_at": session["created_at"],
                "updated_at": session["updated_at"],
                "message_count": len(session_messages),
                "max_sequence": max(
                    (int(row["sequence"]) for row in session_messages),
                    default=None,
                ),
                "has_checkpoint": checkpoint is not None,
                "source_session_present": True,
                "content_hash": _value_hash(content),
            }
        )
    orphan_keys = (set(messages) | set(checkpoints)) - session_keys
    for key in sorted(orphan_keys):
        session_messages = sorted(messages.get(key, []), key=lambda row: int(row["sequence"]))
        checkpoint = checkpoints.get(key)
        timestamps = [
            row["created_at"]
            for row in session_messages
            if row.get("created_at") is not None
        ]
        if checkpoint is not None:
            timestamps.extend(
                value
                for value in (checkpoint.get("created_at"), checkpoint.get("updated_at"))
                if value is not None
            )
        fallback_time = min(timestamps) if timestamps else datetime.fromtimestamp(0)
        manifests.append(
            {
                "agent_id": key[0],
                "session_id": key[1],
                "user_id": None,
                "title": None,
                "created_at": min(timestamps) if timestamps else fallback_time,
                "updated_at": max(timestamps) if timestamps else fallback_time,
                "message_count": len(session_messages),
                "max_sequence": max(
                    (int(row["sequence"]) for row in session_messages),
                    default=None,
                ),
                "has_checkpoint": checkpoint is not None,
                "source_session_present": False,
                "content_hash": _value_hash(
                    {
                        "session": None,
                        "messages": session_messages,
                        "checkpoint": checkpoint,
                    }
                ),
            }
        )
    manifests.sort(key=lambda item: (str(item["agent_id"]), str(item["session_id"])))
    return manifests


def _build_external_artifacts(
    rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]],
) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str, str]] = set()
    for table_name in ("chat_messages", "chat_context_checkpoints"):
        for row in rows_by_table[table_name]:
            payload = row["payload"]
            for path, key, value in _walk_artifact_handles(payload):
                handle = str(value).strip()
                if not handle:
                    continue
                identity = (
                    str(row["agent_id"]),
                    str(row["session_id"]),
                    table_name,
                    key,
                    handle,
                )
                if identity in seen:
                    continue
                seen.add(identity)
                artifacts.append(
                    {
                        "agent_id": identity[0],
                        "session_id": identity[1],
                        "source_table": table_name,
                        "source_row_id": str(row.get("id") or "") or None,
                        "source_sequence": row.get("sequence"),
                        "kind": ARTIFACT_KEY_KINDS[key],
                        "handle": handle,
                        "availability": "not_checked",
                        "locator_path": path,
                    }
                )
    return artifacts


def _walk_artifact_handles(
    value: Any,
    *,
    path: str = "$",
) -> Iterable[tuple[str, str, Any]]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if key in ARTIFACT_KEY_KINDS and isinstance(child, (str, int)):
                yield child_path, key, child
            yield from _walk_artifact_handles(child, path=child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from _walk_artifact_handles(child, path=f"{path}[{index}]")


async def _write_manifests(
    engine: AsyncEngine,
    manifests: Sequence[Mapping[str, Any]],
) -> None:
    if not manifests:
        return
    table = await _reflect_archive_table(engine, "_session_manifest")
    async with engine.begin() as connection:
        await connection.execute(insert(table), list(manifests))


async def _write_external_artifacts(
    engine: AsyncEngine,
    artifacts: Sequence[Mapping[str, Any]],
) -> None:
    if not artifacts:
        return
    table = await _reflect_archive_table(engine, "_external_artifacts")
    async with engine.begin() as connection:
        await connection.execute(insert(table), list(artifacts))


async def _write_anomalies(
    engine: AsyncEngine,
    anomalies: Sequence[Mapping[str, Any]],
) -> None:
    if not anomalies:
        return
    table = await _reflect_archive_table(engine, "_backup_anomalies")
    async with engine.begin() as connection:
        await connection.execute(insert(table), list(anomalies))


async def _write_backup_info(
    engine: AsyncEngine,
    *,
    environment: str,
    exported_at: datetime,
    source_dialect: str,
    revision: str,
    git_commit: str,
    row_counts: Mapping[str, int],
    table_hashes: Mapping[str, str],
    anomaly_count: int,
) -> None:
    manifest = {
        "tables": [
            {
                "name": name,
                "row_count": row_counts[name],
                "content_hash": table_hashes[name],
            }
            for name in LEGACY_SESSION_TABLES
        ],
        "external_artifacts_downloaded": False,
        "relationship_anomaly_count": anomaly_count,
    }
    async with engine.begin() as connection:
        await connection.execute(
            text(
                """
                INSERT INTO _backup_info (
                    id, format_version, environment, exported_at,
                    source_dialect, alembic_revision, git_commit, table_manifest
                ) VALUES (
                    1, :format_version, :environment, :exported_at,
                    :source_dialect, :revision, :git_commit, :table_manifest
                )
                """
            ),
            {
                "format_version": BACKUP_FORMAT_VERSION,
                "environment": environment,
                "exported_at": exported_at.isoformat(),
                "source_dialect": source_dialect,
                "revision": revision,
                "git_commit": git_commit,
                "table_manifest": _canonical_json(manifest),
            },
        )


async def _validate_archive(
    engine: AsyncEngine,
    *,
    revision: str,
    expected_rows: Mapping[str, Sequence[Mapping[str, Any]]],
    expected_manifests: Sequence[Mapping[str, Any]],
) -> None:
    async with engine.connect() as connection:
        integrity = (await connection.execute(text("PRAGMA integrity_check"))).scalar_one()
        if integrity != "ok":
            raise ValueError("Session history backup SQLite integrity check failed")
        stored_revision = (
            await connection.execute(text("SELECT version_num FROM alembic_version"))
        ).scalar_one()
        if stored_revision != revision:
            raise ValueError("Session history backup revision mismatch")

    for table_name, source_rows in expected_rows.items():
        archive_rows = await _read_archive_rows(engine, table_name)
        if len(archive_rows) != len(source_rows):
            raise ValueError(
                f"Session history backup row count mismatch for {table_name}"
            )
        if _rows_hash(archive_rows) != _rows_hash(source_rows):
            raise ValueError(
                f"Session history backup content hash mismatch for {table_name}"
            )
    archive_manifests = await _read_archive_rows(engine, "_session_manifest")
    if _rows_hash(archive_manifests) != _rows_hash(expected_manifests):
        raise ValueError("Session history backup manifest hash mismatch")


async def _read_archive_rows(
    engine: AsyncEngine,
    table_name: str,
) -> list[dict[str, Any]]:
    table = await _reflect_archive_table(engine, table_name)
    if table_name in PRIMARY_KEY_COLUMNS:
        order_names = PRIMARY_KEY_COLUMNS[table_name]
    elif table_name == "_session_manifest":
        order_names = ("agent_id", "session_id")
    else:
        order_names = ("id",)
    async with engine.connect() as connection:
        result = await connection.execute(
            select(table).order_by(*(table.c[name] for name in order_names))
        )
        rows = [dict(row) for row in result.mappings().all()]
    if table_name in JSON_COLUMNS:
        rows = [_normalize_row(table_name, row) for row in rows]
    return rows


async def _reflect_archive_table(engine: AsyncEngine, table_name: str) -> Table:
    metadata = MetaData()
    async with engine.connect() as connection:
        return await connection.run_sync(
            lambda sync_connection: Table(
                table_name,
                metadata,
                autoload_with=sync_connection,
            )
        )


def _rows_hash(rows: Sequence[Mapping[str, Any]]) -> str:
    return _value_hash(list(rows))


def _value_hash(value: Any) -> str:
    return hashlib.sha256(_canonical_json(value).encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=_json_default,
    )


def _json_default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bytes):
        return {"$bytes_hex": value.hex()}
    raise TypeError(f"Unsupported canonical JSON value: {type(value)!r}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _current_git_commit() -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository_root(),
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Export revision-007 DeepAgent session history to a verified SQLite archive"
    )
    parser.add_argument("--config", required=True, help="Path to an environment config file")
    parser.add_argument("--environment", required=True, help="Backup environment label")
    parser.add_argument("--output-root", help="Optional backup root directory")
    return parser


async def _run_cli(args: argparse.Namespace) -> SessionHistoryBackupResult:
    return await export_session_history_backup(
        database_url_from_config(args.config),
        environment=args.environment,
        output_root=args.output_root,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = asyncio.run(_run_cli(args))
    print(f"Session history backup verified: {result.path}")
    print(f"SHA256: {result.checksum_path}")
    print(
        "Rows: "
        + ", ".join(
            f"{name}={count}" for name, count in result.row_counts.items()
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
