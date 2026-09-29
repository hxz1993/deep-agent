from __future__ import annotations

from deep_agent.db.reference_integrity import acquire_write_barrier

import asyncio
import json
import shutil
import zipfile
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping, Sequence

from loguru import logger
from sqlalchemy import Boolean, DateTime, Float, Integer, func, inspect, select, text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from deep_agent.capabilities.codes import canonical_tool_package_codes
from deep_agent.db.configuration_transfer import (
    SESSION_TABLES,
    CACHE_TABLES,
    backup_scope_manifest,
    configuration_table_names,
    validate_table_classification,
)
from deep_agent.db.models import Base
from deep_agent.integrations.catalog import get_integration_definition
from deep_agent.schemas.integration import validate_integration_settings
from deep_agent.files.storage import FileStore, StoredFile
from deep_agent.skills.package import MAX_ZIP_BYTES, SkillPackageError, parse_skill_zip
from deepfos_platform.client.platform import FILE_TYPE_SKILL_PACKAGE


MAX_CONFIGURATION_BACKUP_BYTES = 64 * 1024 * 1024
MAX_SKILLS_BUNDLE_BYTES = 256 * 1024 * 1024
MAX_SKILLS_PER_IMPORT = 100
_IMPORT_LOCK = asyncio.Lock()
_MYSQL_LOCK_NAME = "deep-agent-configuration-import"
_POSTGRES_LOCK_KEY = 2_024_070_230_001


class ConfigurationImportError(ValueError):
    pass


class ConfigurationImportConflictError(ConfigurationImportError):
    pass


class ConfigurationImportBusyError(ConfigurationImportConflictError):
    pass


class ConfigurationImportFileStoreError(ConfigurationImportError):
    pass


@dataclass(frozen=True)
class BackupSource:
    environment: str
    exported_at: str
    source_dialect: str
    revision: str
    git_commit: str


@dataclass(frozen=True)
class SkillPayload:
    code: str
    file_name: str
    raw: bytes


@dataclass(frozen=True)
class ConfigurationImportPlan:
    source: BackupSource
    target_revision: str
    rows: dict[str, list[dict[str, Any]]]
    row_counts: dict[str, int]
    agent_ids: tuple[str, ...]
    skills: tuple[SkillPayload, ...]


@dataclass(frozen=True)
class ConfigurationImportResult:
    plan: ConfigurationImportPlan
    skill_files_uploaded: int


@dataclass(frozen=True)
class _UploadedSkill:
    code: str
    payload: SkillPayload
    stored: StoredFile


def _normalize_ondelete(value: Any) -> str | None:
    if value is None:
        return None
    normalized = str(value).strip().upper().replace("_", " ")
    # MySQL reflects the default RESTRICT action as no option. They are
    # semantically identical, while CASCADE/SET NULL must still match exactly.
    return None if normalized == "RESTRICT" else normalized


def _type_family(value: Any) -> str:
    candidate = getattr(value, "impl", value)
    rendered = str(candidate).strip().upper().replace(" ", "")
    type_name = type(candidate).__name__.upper()
    if (
        "BOOL" in rendered
        or rendered.startswith("TINYINT(1)")
        or type_name == "TINYINT"
    ):
        return "boolean"
    if isinstance(candidate, Boolean):
        return "boolean"
    if isinstance(candidate, Integer):
        return "integer"
    if isinstance(candidate, Float):
        return "float"
    if isinstance(candidate, DateTime):
        return "datetime"
    return "text"


def _expected_foreign_keys(table_name: str) -> set[tuple[Any, ...]]:
    table = Base.metadata.tables[table_name]
    return {
        (
            tuple(element.parent.name for element in constraint.elements),
            constraint.referred_table.name,
            tuple(element.column.name for element in constraint.elements),
            _normalize_ondelete(constraint.ondelete),
        )
        for constraint in table.foreign_key_constraints
    }


def _actual_foreign_keys(inspector: Any, table_name: str) -> set[tuple[Any, ...]]:
    return {
        (
            tuple(item.get("constrained_columns") or ()),
            str(item.get("referred_table") or ""),
            tuple(item.get("referred_columns") or ()),
            _normalize_ondelete((item.get("options") or {}).get("ondelete")),
        )
        for item in inspector.get_foreign_keys(table_name)
    }


def _validate_table_shape(inspector: Any, table_name: str) -> None:
    table = Base.metadata.tables[table_name]
    actual_columns = inspector.get_columns(table_name)
    actual_by_name = {str(item["name"]): item for item in actual_columns}
    expected_names = [column.name for column in table.columns]
    if set(actual_by_name) != set(expected_names):
        raise ConfigurationImportError(
            f"Database table columns do not match current code: {table_name}"
        )

    for column in table.columns:
        actual = actual_by_name[column.name]
        if bool(actual.get("nullable", True)) != bool(column.nullable):
            raise ConfigurationImportError(
                f"Database column nullability does not match current code: "
                f"{table_name}.{column.name}"
            )
        if _type_family(actual["type"]) != _type_family(column.type):
            raise ConfigurationImportError(
                f"Database column type does not match current code: "
                f"{table_name}.{column.name}"
            )

    actual_pk = tuple(
        inspector.get_pk_constraint(table_name).get("constrained_columns") or ()
    )
    expected_pk = tuple(column.name for column in table.primary_key.columns)
    if actual_pk != expected_pk:
        raise ConfigurationImportError(
            f"Database primary key does not match current code: {table_name}"
        )

    if _actual_foreign_keys(inspector, table_name) != _expected_foreign_keys(table_name):
        raise ConfigurationImportError(
            f"Database foreign keys do not match current code: {table_name}"
        )


def _validate_schema_sync(sync_connection: Any, *, backup: bool) -> None:
    inspector = inspect(sync_connection)
    expected = set(Base.metadata.tables) | {"alembic_version"}
    if backup:
        expected.add("_backup_info")
    actual = set(inspector.get_table_names())
    if actual != expected:
        raise ConfigurationImportError(
            "Database table scope does not match the current configuration contract"
        )
    for table_name in Base.metadata.tables:
        _validate_table_shape(inspector, table_name)
    revision_columns = {
        str(item["name"]) for item in inspector.get_columns("alembic_version")
    }
    if revision_columns != {"version_num"}:
        raise ConfigurationImportError("Invalid alembic_version table")


async def _read_revision(connection: AsyncConnection) -> str:
    rows = (
        await connection.execute(text("SELECT version_num FROM alembic_version"))
    ).scalars().all()
    if len(rows) != 1 or not str(rows[0]).strip():
        raise ConfigurationImportError(
            "Database must contain exactly one Alembic revision"
        )
    return str(rows[0]).strip()


async def _table_count(connection: AsyncConnection, table_name: str) -> int:
    table = Base.metadata.tables[table_name]
    return int(
        (
            await connection.execute(select(func.count()).select_from(table))
        ).scalar_one()
    )


def _expected_primary_keys(
    table_name: str,
    rows: Sequence[Mapping[str, Any]],
) -> set[tuple[Any, ...]]:
    table = Base.metadata.tables[table_name]
    names = tuple(column.name for column in table.primary_key.columns)
    return {tuple(row[name] for name in names) for row in rows}


async def _actual_primary_keys(
    connection: AsyncConnection,
    table_name: str,
) -> set[tuple[Any, ...]]:
    table = Base.metadata.tables[table_name]
    columns = tuple(table.primary_key.columns)
    result = await connection.execute(select(*columns))
    return {tuple(row) for row in result.all()}


def _validate_channel_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    from deep_agent.channels.app_context import validate_channel_settings
    from deep_agent.schemas.channel import validate_webhook_credentials
    for row in rows:
        channel_type = row.get("channel_type")
        expected_transport = "webhook" if channel_type == "wecom_app" else "websocket"
        if channel_type not in {"wecom", "feishu", "dingtalk", "wecom_app"} or row.get("transport") != expected_transport:
            raise ConfigurationImportError("channel_accounts contains an unsupported channel type or transport")
        try:
            settings = row.get("settings") or {}
            if not isinstance(settings, dict):
                raise ValueError("settings must be an object")
            validate_channel_settings(channel_type, settings)
            if channel_type == "wecom_app":
                if row.get("bot_id") or row.get("secret"):
                    raise ValueError("Application channel must not contain bot credentials")
                validate_webhook_credentials(row.get("webhook_token"), row.get("webhook_aes_key"))
            elif not row.get("bot_id") or not row.get("secret") or row.get("webhook_token") or row.get("webhook_aes_key"):
                raise ValueError("Robot credentials are required and must not contain webhook credentials")
        except (ValueError, TypeError) as exc:
            raise ConfigurationImportError("channel_accounts contains an invalid channel configuration") from exc


def _validate_integration_rows(rows: Sequence[Mapping[str, Any]]) -> None:
    for row in rows:
        if get_integration_definition(row.get("code")) is None:
            raise ConfigurationImportError("integration_settings contains an unknown integration code")
        try:
            value = validate_integration_settings(row.get("settings"))
        except ValueError as exc:
            raise ConfigurationImportError("integration_settings contains an invalid JSON configuration") from exc
        if not value:
            raise ConfigurationImportError("integration_settings must not contain empty configurations")


async def _validate_empty_sessions(connection: AsyncConnection, *, target: bool) -> None:
    counts = {
        table_name: await _table_count(connection, table_name)
        for table_name in SESSION_TABLES
    }
    if any(counts.values()):
        location = "Target database" if target else "Configuration backup"
        error = ConfigurationImportConflictError if target else ConfigurationImportError
        raise error(f"{location} contains conversation data")


async def _validate_logical_references(
    connection: AsyncConnection,
    *,
    prefix: str,
) -> None:
    from deep_agent.db.reference_schema import logical_reference_violations
    violations = await connection.run_sync(logical_reference_violations)
    if violations:
        details = "; ".join(
            f"{item['table']}.{item['column']} -> "
            f"{item['parent_table']}.{item['parent_column']}: {item['count']}"
            for item in violations
        )
        raise ConfigurationImportError(f"{prefix} contains invalid logical references: {details}")


async def inspect_configuration_backup(
    path: Path,
) -> tuple[BackupSource, dict[str, list[dict[str, Any]]]]:
    validate_table_classification()
    if not path.is_file():
        raise ConfigurationImportError("Configuration backup file is missing")
    if path.stat().st_size > MAX_CONFIGURATION_BACKUP_BYTES:
        raise ConfigurationImportError("Configuration backup exceeds 64 MiB")

    source_engine = create_async_engine(
        f"sqlite+aiosqlite:///{path.resolve().as_posix()}",
        pool_pre_ping=True,
    )
    try:
        async with source_engine.connect() as connection:
            await connection.execute(text("PRAGMA query_only=ON"))
            integrity = (
                await connection.execute(text("PRAGMA integrity_check"))
            ).scalar_one()
            if integrity != "ok":
                raise ConfigurationImportError("SQLite integrity check failed")
            foreign_key_violations = (
                await connection.execute(text("PRAGMA foreign_key_check"))
            ).all()
            if foreign_key_violations:
                raise ConfigurationImportError(
                    "Configuration backup contains invalid foreign key references"
                )
            await connection.run_sync(
                lambda sync_connection: _validate_schema_sync(
                    sync_connection,
                    backup=True,
                )
            )
            revision = await _read_revision(connection)
            info_rows = (
                await connection.execute(
                    text(
                        "SELECT environment, exported_at, source_dialect, "
                        "alembic_revision, git_commit, scope_manifest "
                        "FROM _backup_info"
                    )
                )
            ).mappings().all()
            if len(info_rows) != 1:
                raise ConfigurationImportError(
                    "Configuration backup must contain exactly one metadata row"
                )
            info = info_rows[0]
            if str(info["alembic_revision"]) != revision:
                raise ConfigurationImportError(
                    "Backup metadata revision does not match alembic_version"
                )
            try:
                manifest = json.loads(str(info["scope_manifest"]))
            except (TypeError, ValueError) as exc:
                raise ConfigurationImportError("Invalid backup scope manifest") from exc
            config_tables = configuration_table_names()
            if manifest != backup_scope_manifest(config_tables):
                raise ConfigurationImportError(
                    "Backup scope manifest does not match the current contract"
                )
            await _validate_empty_sessions(connection, target=False)
            for table_name in CACHE_TABLES:
                if await _table_count(connection, table_name):
                    raise ConfigurationImportError("Configuration backup contains member cache data")
            await _validate_logical_references(connection, prefix="Backup")

            rows: dict[str, list[dict[str, Any]]] = {}
            for table_name in config_tables:
                table = Base.metadata.tables[table_name]
                result = await connection.execute(select(table))
                rows[table_name] = [dict(row) for row in result.mappings().all()]
            for row in rows.get("llm_providers", []):
                if row.get("auth_method") == "oauth":
                    row["credential_json"] = None
                    row["auth_status"] = "unconfigured"
                    row["auth_revision"] = 0
            _validate_integration_rows(rows.get("integration_settings", []))
            _validate_channel_rows(rows.get("channel_accounts", []))
            for row in rows.get("agents", []):
                row["capabilities"] = canonical_tool_package_codes(
                    row.get("capabilities") or []
                )

            source = BackupSource(
                environment=str(info["environment"]),
                exported_at=str(info["exported_at"]),
                source_dialect=str(info["source_dialect"]),
                revision=revision,
                git_commit=str(info["git_commit"]),
            )
            if not all(
                (
                    source.environment,
                    source.exported_at,
                    source.source_dialect,
                    source.revision,
                    source.git_commit,
                )
            ):
                raise ConfigurationImportError("Backup metadata is incomplete")
            return source, rows
    except ConfigurationImportError:
        raise
    except Exception as exc:
        raise ConfigurationImportError("Unable to read configuration backup") from exc
    finally:
        await source_engine.dispose()


def _skill_rows(rows: Mapping[str, Sequence[Mapping[str, Any]]]) -> dict[str, Mapping[str, Any]]:
    return {
        str(row["code"]): row
        for row in rows.get("skill_packages", ())
    }


def inspect_skills_bundle(
    path: Path | None,
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> tuple[SkillPayload, ...]:
    expected_rows = _skill_rows(rows)
    if not expected_rows:
        if path is not None:
            raise ConfigurationImportError(
                "Skills bundle was provided but the backup contains no uploaded Skills"
            )
        return ()
    if path is None:
        raise ConfigurationImportError(
            "Backup contains uploaded Skills; provide the matching Skills export bundle"
        )
    if not path.is_file() or path.stat().st_size > MAX_SKILLS_BUNDLE_BYTES:
        raise ConfigurationImportError("Skills bundle is missing or exceeds 256 MiB")
    if len(expected_rows) > MAX_SKILLS_PER_IMPORT:
        raise ConfigurationImportError(
            f"Skills bundle exceeds {MAX_SKILLS_PER_IMPORT} packages"
        )

    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as exc:
        raise ConfigurationImportError("Skills bundle is not a valid ZIP archive") from exc

    parsed_roots: list[Path] = []
    try:
        with archive:
            names = archive.namelist()
            if len(names) != len(set(names)):
                raise ConfigurationImportError("Skills bundle contains duplicate paths")
            if "manifest.json" not in names:
                raise ConfigurationImportError("Skills bundle is missing manifest.json")
            try:
                manifest = json.loads(archive.read("manifest.json"))
            except (KeyError, UnicodeError, ValueError) as exc:
                raise ConfigurationImportError("Invalid Skills bundle manifest") from exc
            if (
                manifest.get("format") != "deepagent-skill-export"
                or manifest.get("schema_version") != "1.0"
                or not isinstance(manifest.get("skills"), list)
            ):
                raise ConfigurationImportError("Unsupported Skills bundle format")
            manifest_by_code: dict[str, Mapping[str, Any]] = {}
            for item in manifest["skills"]:
                if not isinstance(item, dict) or not str(item.get("code") or ""):
                    raise ConfigurationImportError("Invalid Skills bundle entry")
                code = str(item["code"])
                if code in manifest_by_code:
                    raise ConfigurationImportError("Duplicate Skill code in bundle manifest")
                manifest_by_code[code] = item
            if set(manifest_by_code) != set(expected_rows):
                raise ConfigurationImportError(
                    "Skills bundle codes do not match the configuration backup"
                )

            allowed_names = {"manifest.json"}
            payloads: list[SkillPayload] = []
            for code in sorted(expected_rows):
                row = expected_rows[code]
                item = manifest_by_code[code]
                package_file = str(item.get("package_file") or "")
                if package_file != f"skills/{code}.zip":
                    raise ConfigurationImportError(
                        f"Invalid package path for Skill {code}"
                    )
                allowed_names.add(package_file)
                try:
                    info = archive.getinfo(package_file)
                except KeyError as exc:
                    raise ConfigurationImportError(
                        f"Skills bundle is missing package {code}"
                    ) from exc
                if info.file_size > MAX_ZIP_BYTES:
                    raise ConfigurationImportError(
                        f"Skill package exceeds size limit: {code}"
                    )
                raw = archive.read(info)
                try:
                    parsed = parse_skill_zip(raw)
                except SkillPackageError as exc:
                    raise ConfigurationImportError(
                        f"Invalid Skill package in bundle: {code}"
                    ) from exc
                parsed_roots.append(parsed.package_root.parent)
                if (
                    parsed.code != code
                    or parsed.content_hash != str(row["content_hash"])
                    or parsed.content_hash != str(item.get("content_hash") or "")
                    or parsed.schema_version != str(row["schema_version"])
                    or parsed.manifest != row["manifest"]
                    or parsed.resource_index != row["resource_index"]
                    or parsed.contains_scripts != bool(row["contains_scripts"])
                ):
                    raise ConfigurationImportError(
                        f"Skill package does not match backup metadata: {code}"
                    )
                file_name = str(item.get("original_file_name") or f"{code}.zip")
                payloads.append(SkillPayload(code=code, file_name=file_name, raw=raw))
            if set(names) != allowed_names:
                raise ConfigurationImportError(
                    "Skills bundle contains files not declared by its manifest"
                )
            return tuple(payloads)
    finally:
        for root in parsed_roots:
            shutil.rmtree(root, ignore_errors=True)


async def _validate_target(
    connection: AsyncConnection,
    *,
    source_revision: str,
) -> str:
    await connection.run_sync(
        lambda sync_connection: _validate_schema_sync(
            sync_connection,
            backup=False,
        )
    )
    target_revision = await _read_revision(connection)
    if target_revision != source_revision:
        raise ConfigurationImportConflictError(
            "Backup revision does not match the target database revision"
        )
    await _validate_empty_sessions(connection, target=True)
    await _validate_logical_references(connection, prefix="Target database")
    return target_revision


async def build_configuration_import_plan(
    backup_path: Path,
    *,
    target_engine: AsyncEngine,
    skills_bundle_path: Path | None = None,
    active_run_count: int = 0,
) -> ConfigurationImportPlan:
    if active_run_count:
        raise ConfigurationImportConflictError(
            "Target DeepAgent has active runs"
        )
    source, rows = await inspect_configuration_backup(backup_path)
    skills = await asyncio.to_thread(inspect_skills_bundle, skills_bundle_path, rows)
    async with target_engine.connect() as connection:
        target_revision = await _validate_target(
            connection,
            source_revision=source.revision,
        )
    row_counts = {name: len(items) for name, items in rows.items()}
    agent_ids = tuple(sorted(str(row["id"]) for row in rows.get("agents", ())))
    return ConfigurationImportPlan(
        source=source,
        target_revision=target_revision,
        rows=rows,
        row_counts=row_counts,
        agent_ids=agent_ids,
        skills=skills,
    )


async def _acquire_database_lock(connection: AsyncConnection) -> bool:
    dialect = connection.dialect.name
    if dialect == "mysql":
        acquired = (
            await connection.execute(
                text("SELECT GET_LOCK(:name, 0)"),
                {"name": _MYSQL_LOCK_NAME},
            )
        ).scalar_one()
        await connection.commit()
        return int(acquired or 0) == 1
    if dialect == "postgresql":
        acquired = (
            await connection.execute(
                text("SELECT pg_try_advisory_lock(:key)"),
                {"key": _POSTGRES_LOCK_KEY},
            )
        ).scalar_one()
        await connection.commit()
        return bool(acquired)
    return True


async def _release_database_lock(connection: AsyncConnection) -> None:
    dialect = connection.dialect.name
    if dialect == "mysql":
        await connection.execute(
            text("SELECT RELEASE_LOCK(:name)"),
            {"name": _MYSQL_LOCK_NAME},
        )
        await connection.commit()
    elif dialect == "postgresql":
        await connection.execute(
            text("SELECT pg_advisory_unlock(:key)"),
            {"key": _POSTGRES_LOCK_KEY},
        )
        await connection.commit()


async def _delete_uploaded_skills(
    file_store: FileStore,
    uploaded: Sequence[_UploadedSkill],
    request_headers: Mapping[str, str],
) -> None:
    for item in reversed(uploaded):
        try:
            await file_store.delete(item.stored.id, request_headers)
        except Exception as exc:
            logger.warning(
                "Configuration import Skill cleanup failed code={} error_type={}",
                item.code,
                type(exc).__name__,
            )
            continue


async def _upload_skills(
    plan: ConfigurationImportPlan,
    *,
    file_store: FileStore,
    request_headers: Mapping[str, str],
) -> tuple[_UploadedSkill, ...]:
    uploaded: list[_UploadedSkill] = []
    try:
        for payload in plan.skills:
            stored = await file_store.upload(
                file_type=FILE_TYPE_SKILL_PACKAGE,
                file_name=payload.file_name,
                mime="application/zip",
                data=payload.raw,
                request_headers=request_headers,
            )
            uploaded.append(
                _UploadedSkill(
                    code=payload.code,
                    payload=payload,
                    stored=stored,
                )
            )
    except Exception as exc:
        await _delete_uploaded_skills(file_store, uploaded, request_headers)
        raise ConfigurationImportFileStoreError(
            "Unable to upload Skill packages to the target file store"
        ) from exc
    return tuple(uploaded)


def _rows_with_uploaded_skills(
    plan: ConfigurationImportPlan,
    uploaded: Sequence[_UploadedSkill],
) -> dict[str, list[dict[str, Any]]]:
    rows = {
        name: [dict(item) for item in items]
        for name, items in plan.rows.items()
    }
    by_code = {item.code: item for item in uploaded}
    for row in rows.get("skill_packages", []):
        item = by_code[str(row["code"])]
        row["platform_file_id"] = item.stored.id
        row["file_name"] = item.stored.file_name
        row["file_size"] = item.stored.file_size
    return rows


async def _insert_rows(
    connection: AsyncConnection,
    table_name: str,
    rows: Sequence[Mapping[str, Any]],
) -> None:
    if not rows:
        return
    table = Base.metadata.tables[table_name]
    batch_size = 500
    for offset in range(0, len(rows), batch_size):
        await connection.execute(table.insert(), list(rows[offset : offset + batch_size]))


async def _replace_configuration(
    connection: AsyncConnection,
    *,
    plan: ConfigurationImportPlan,
    rows: Mapping[str, Sequence[Mapping[str, Any]]],
) -> None:
    async with connection.begin():
        if connection.dialect.name == "sqlite":
            # Read validation must share the database writer transaction with
            # replacement, including when the target currently has no rows.
            await connection.execute(text("BEGIN IMMEDIATE"))
        await acquire_write_barrier(connection, exclusive=True)
        await _validate_target(
            connection,
            source_revision=plan.source.revision,
        )
        table_names = configuration_table_names()
        # Profiles are rebuildable and must not survive replacement of their application identity.
        for table_name in CACHE_TABLES:
            await connection.execute(Base.metadata.tables[table_name].delete())
        for table_name in reversed(table_names):
            await connection.execute(Base.metadata.tables[table_name].delete())
        for table_name in table_names:
            await _insert_rows(connection, table_name, rows[table_name])
        await _validate_empty_sessions(connection, target=True)
        await _validate_logical_references(connection, prefix="Imported configuration")
        for table_name, expected in plan.row_counts.items():
            actual = await _table_count(connection, table_name)
            if actual != expected:
                raise ConfigurationImportError(
                    f"Imported row count mismatch for {table_name}"
                )
            if await _actual_primary_keys(
                connection,
                table_name,
            ) != _expected_primary_keys(table_name, rows[table_name]):
                raise ConfigurationImportError(
                    f"Imported primary key mismatch for {table_name}"
                )
        if await _read_revision(connection) != plan.target_revision:
            raise ConfigurationImportError(
                "Target Alembic revision changed during configuration import"
            )


async def apply_configuration_import(
    plan: ConfigurationImportPlan,
    *,
    target_engine: AsyncEngine,
    file_store: FileStore,
    request_headers: Mapping[str, str],
    active_run_count: int = 0,
) -> ConfigurationImportResult:
    _validate_integration_rows(plan.rows.get("integration_settings", []))
    _validate_channel_rows(plan.rows.get("channel_accounts", []))
    if active_run_count:
        raise ConfigurationImportConflictError("Target DeepAgent has active runs")
    if _IMPORT_LOCK.locked():
        raise ConfigurationImportBusyError("A configuration import is already running")

    async with _IMPORT_LOCK:
        async with target_engine.connect() as connection:
            if not await _acquire_database_lock(connection):
                raise ConfigurationImportBusyError(
                    "A configuration import is already running"
                )
            uploaded: tuple[_UploadedSkill, ...] = ()
            committed = False
            try:
                await _validate_target(
                    connection,
                    source_revision=plan.source.revision,
                )
                # SQLAlchemy autobegins for read-only validation. End that
                # transaction before opening the single replacement transaction.
                await connection.rollback()
                uploaded = await _upload_skills(
                    plan,
                    file_store=file_store,
                    request_headers=request_headers,
                )
                rows = _rows_with_uploaded_skills(plan, uploaded)
                await _replace_configuration(
                    connection,
                    plan=plan,
                    rows=rows,
                )
                committed = True
                from deep_agent.services.codex_provider import evict_all_providers
                evict_all_providers()
                updated_plan = replace(plan, rows=rows)
                return ConfigurationImportResult(
                    plan=updated_plan,
                    skill_files_uploaded=len(uploaded),
                )
            except ConfigurationImportError:
                raise
            except Exception as exc:
                raise ConfigurationImportError(
                    "Configuration import failed and database changes were rolled back"
                ) from exc
            finally:
                if uploaded and not committed:
                    await _delete_uploaded_skills(
                        file_store,
                        uploaded,
                        request_headers,
                    )
                try:
                    await _release_database_lock(connection)
                except Exception as exc:
                    # Closing the dedicated connection also releases MySQL and
                    # PostgreSQL session-level advisory locks. Do not mask a
                    # committed import or its original failure.
                    logger.warning(
                        "Configuration import database lock release failed "
                        "error_type={}",
                        type(exc).__name__,
                    )


__all__ = [
    "MAX_CONFIGURATION_BACKUP_BYTES",
    "MAX_SKILLS_BUNDLE_BYTES",
    "BackupSource",
    "ConfigurationImportBusyError",
    "ConfigurationImportConflictError",
    "ConfigurationImportError",
    "ConfigurationImportFileStoreError",
    "ConfigurationImportPlan",
    "ConfigurationImportResult",
    "apply_configuration_import",
    "build_configuration_import_plan",
    "inspect_configuration_backup",
    "inspect_skills_bundle",
]
